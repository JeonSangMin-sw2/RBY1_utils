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
type ZoomRange = { start: number; end: number };

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

function jointFromSeries(name: string): string | null {
  const suffixes = [
    "_target_fb_gain",
    "_target_ff_tq",
    "_target_pos",
    "_target_vel",
    "_state",
    "_pos",
    "_vel",
    "_cur",
    "_tq",
    "_temperature",
    "_temp",
    "_motor_temp",
    "_drive_temp",
  ];
  const suffix = suffixes.find((item) => name.endsWith(item));
  return suffix ? name.slice(0, -suffix.length) : null;
}

function namesFor(category: SignalCategory, joint: string, available: Set<string>): string[] {
  const candidates: Record<Exclude<SignalCategory, "system">, string[]> = {
    position: [`${joint}_pos`, `${joint}_target_pos`],
    velocity: [`${joint}_vel`, `${joint}_target_vel`],
    current: [`${joint}_cur`, `${joint}_current`],
    torque: [`${joint}_tq`, `${joint}_target_ff_tq`, `${joint}_torque`],
    temperature: [`${joint}_temperature`, `${joint}_temp`, `${joint}_motor_temp`, `${joint}_drive_temp`],
    state: [`${joint}_state`, `${joint}_motor_state`],
    gain: [`${joint}_target_fb_gain`],
  };
  return (category === "system" ? SYSTEM_SERIES : candidates[category]).filter((name) => available.has(name));
}

function categoryAvailable(category: SignalCategory, joints: string[], available: Set<string>): boolean {
  return category === "system"
    ? SYSTEM_SERIES.some((name) => available.has(name))
    : joints.some((joint) => namesFor(category, joint, available).length > 0);
}

function eligibleJointsFor(category: SignalCategory, joints: string[], available: Set<string>): string[] {
  return category === "system"
    ? []
    : joints.filter((name) => namesFor(category, name, available).length > 0);
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "--";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  const hrs = Math.floor(seconds / 3600);
  const mins = Math.floor((seconds % 3600) / 60);
  const secs = seconds % 60;
  if (hrs > 0) {
    return `${hrs}:${String(mins).padStart(2, "0")}:${secs.toFixed(3).padStart(6, "0")}`;
  }
  return `${String(mins).padStart(2, "0")}:${secs.toFixed(3).padStart(6, "0")}`;
}

function formatAxisTime(value: number, start: number): string {
  if (value > 1_000_000_000) {
    const date = new Date(value * 1000);
    return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}.${String(date.getMilliseconds()).padStart(3, "0")}`;
  }
  return `${(value - start).toFixed(3)}s`;
}

type SystemTooltipParam = {
  axisValue: number | string;
  dataIndex: number;
  seriesIndex: number;
  marker: string;
  seriesName: string;
  value?: number | [number, number];
};

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[character] ?? character);
}

function systemTooltip(
  value: SystemTooltipParam | SystemTooltipParam[],
  displayed: DisplaySeries[],
  start: number,
): string {
  const params = Array.isArray(value) ? value : [value];
  const timestamp = Number(params[0]?.axisValue ?? start);
  const rows = params.map((item) => {
    const state = displayed[item.seriesIndex]?.semanticPoints?.[item.dataIndex];
    const stateText = state
      ? `${state.name} (${state.label}, 원본 ${state.rawValue})`
      : "상태 정보 없음";
    return `${item.marker}${escapeHtml(item.seriesName)}: ${escapeHtml(stateText)}`;
  });
  return [`<strong>${escapeHtml(formatAxisTime(timestamp, start))}</strong>`, ...rows].join("<br/>");
}

function signalTooltip(
  value: SystemTooltipParam | SystemTooltipParam[],
  start: number,
  unit?: string,
): string {
  const params = Array.isArray(value) ? value : [value];
  const timestamp = Number(params[0]?.axisValue ?? start);
  const rows = params.map((item) => {
    const raw = Array.isArray(item.value) ? item.value[1] : item.value;
    const shown = typeof raw === "number" && Number.isFinite(raw) ? raw.toFixed(4) : "-";
    return `${item.marker}${escapeHtml(item.seriesName)}: ${shown}${unit ? ` ${escapeHtml(unit)}` : ""}`;
  });
  return [`<strong>${escapeHtml(formatAxisTime(timestamp, start))}</strong>`, ...rows].join("<br/>");
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
      labels.set(lane, `${joint} · ${definition.name}`);
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
    labels.set(lane, `${systemSeriesLabel(entry.seriesName)} · ${entry.definition.name}`);
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
  if (bit.kind === "core_fault") return "coreFaultBit";
  if (bit.kind === "diagnostic") return "diagnosticBit";
  if (bit.kind === "reserved") return "reservedBit";
  return "normalBit";
}

function stateEquation(bits: MotorBit[]): string {
  if (!bits.length) return "";
  const values = bits.map((item) => item.value.toLocaleString("ko-KR")).join(" + ");
  return `${values} = ${bits.map((item) => item.name).join(" + ")}`;
}

function MotorBitReference({ definitions }: { definitions: MotorBit[] }) {
  const documented = definitions.filter((item) => item.bit <= 18);
  if (!documented.length) return null;
  return <details className="motorBitReference">
    <summary>Motor State 전체 비트 정의</summary>
    <div className="motorBitTableWrap">
      <table>
        <thead><tr><th>비트</th><th>값</th><th>이름</th><th>의미</th><th>Core 판정</th></tr></thead>
        <tbody>
          {documented.map((item) => <tr className={item.kind} key={item.bit}>
            <td>{item.bit}</td><td>{item.value.toLocaleString("ko-KR")}</td><td>{item.name}</td><td>{item.label}</td>
            <td>{item.core_fault ? "Motor Fault 판정 대상" : "-"}</td>
          </tr>)}
          <tr className="reserved"><td>19~31</td><td>-</td><td>reserved</td><td>예약 비트</td><td>-</td></tr>
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
}: {
  category: SignalCategory;
  series: CsvSeries[];
  definitions: MotorBit[];
  contract?: MotorStateContract;
  systemContract?: SystemStateContract;
}) {
  if (category !== "state" && category !== "system") return null;
  if (category === "system") {
    if (!systemContract) return null;
    const resolvedSystemContract = systemContract;
    return <section className="stateDecoder" aria-label="전원 및 제어 상태 해석">
      <div className="stateDecoderHead"><h3>상태 값 해석</h3><p>그래프와 원본 값에 동일한 상태 정의를 적용합니다.</p></div>
      <div className="systemStateGrid">
        {series.map((item) => {
          const values = [...new Set(item.points.map(([, value]) => Math.trunc(value)))];
          return <div key={item.name}><strong>{systemSeriesLabel(item.name)}</strong><p>{values.map((value) => {
            const state = systemStateDefinition(item.name, value, resolvedSystemContract);
            return `${value} = ${state.name} (${state.label})`;
          }).join(" · ")}</p></div>;
        })}
      </div>
    </section>;
  }

  if (!series.length) return null;
  return <section className="stateDecoder" aria-label="모터 상태 비트 해석">
    <div className="stateDecoderHead"><h3>모터 상태 비트 해석</h3><p>원본 정수값을 RBMotor 상태 비트와 매칭합니다.</p></div>
    {series.map((raw) => {
      const values = [...new Set(raw.points.map(([, value]) => Math.trunc(value)))];
      return <section className="stateJointGroup" key={raw.name}>
        <h4>{jointFromSeries(raw.name) ?? raw.name}</h4>
        <div className="stateValueList">
          {values.map((value) => {
            const bits = motorBits(value, definitions);
            const hasCoreFault = bits.some((item) => item.kind === "core_fault");
            const hasDiagnostic = bits.some((item) => item.kind === "diagnostic");
            const valueClass = hasCoreFault ? "hasCoreFault" : hasDiagnostic ? "hasDiagnostic" : "";
            return <div className={valueClass} key={`${raw.name}:${value}`}>
              <code>{value} · 0x{motorStateMask(value).toString(16).toUpperCase()}</code>
              <div>{bits.length ? bits.map((item) => <span className={bitClass(item)} key={item.bit} title={`bit ${item.bit} · 값 ${item.value}`}>
                {item.name}<small>{item.label}</small>
              </span>) : <span className="normalBit">활성 비트 없음</span>}</div>
              {bits.length > 0 && <p className="stateEquation">{stateEquation(bits)}</p>}
            </div>;
          })}
        </div>
      </section>;
    })}
    {contract && <div className="motorStateGuide">
      <div><strong>Core Motor Fault 판정 대상</strong><span>{contract.core_fault_names.join(" · ")} (비트 {contract.core_fault_bits.join(", ")})</span></div>
      <div><strong>기타 상태 비트</strong><span>CSV에는 기록되지만 모두 Core의 Motor Fault 판정 조건에 포함되는 것은 아닙니다.</span></div>
      <ul>
        <li>{contract.temperature_note}</li>
        <li>{contract.dynamixel_head_note}</li>
        <li>비트 {contract.reserved_range}은 예약 영역입니다.</li>
      </ul>
    </div>}
    <MotorBitReference definitions={definitions} />
  </section>;
}

function CsvPlot({
  category,
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
  const lanes = useMemo(() => {
    if (!payload) return null;
    if (category === "state" && series.length) {
      return stateLanes(series, payload.motor_state_bits);
    }
    if (category === "system") {
      return systemLanes(series, payload.system_state_contract);
    }
    return null;
  }, [category, payload, series]);
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

  useEffect(() => {
    zoomCallbackRef.current = onZoomRangeChange;
  }, [onZoomRangeChange]);

  useEffect(() => {
    cursorCallbackRef.current = onCursorChange;
  }, [onCursorChange]);

  useEffect(() => {
    if (!chartNode.current) return;
    const chart = init(chartNode.current);
    chartRef.current = chart;
    const handleZoom = (event: unknown) => {
      const value = event as { start?: number; end?: number; batch?: { start?: number; end?: number }[] };
      const range = value.batch?.[0] ?? value;
      if (typeof range.start !== "number" || typeof range.end !== "number") return;
      zoomCallbackRef.current({ start: range.start, end: range.end });
    };
    chart.on("datazoom", handleZoom);

    // Track mouse dragging to prevent clicks during zoom/pan
    let isDragging = false;
    let downPos = { x: 0, y: 0 };
    const handleMouseDown = (e: { offsetX: number; offsetY: number }) => {
      isDragging = false;
      downPos = { x: e.offsetX, y: e.offsetY };
    };
    const handleMouseMove = (e: { offsetX: number; offsetY: number }) => {
      if (Math.abs(e.offsetX - downPos.x) > 4 || Math.abs(e.offsetY - downPos.y) > 4) {
        isDragging = true;
      }
    };
    const handleMouseUp = (event: { offsetX: number; offsetY: number }) => {
      if (isDragging) return;
      const pointInPixel = [event.offsetX, event.offsetY];
      if (chart.containPixel("grid", pointInPixel)) {
        const pointInGrid = chart.convertFromPixel({ seriesIndex: 0 }, pointInPixel);
        if (pointInGrid && typeof pointInGrid[0] === "number") {
          cursorCallbackRef.current?.(pointInGrid[0]);
        }
      }
    };
    chart.getZr().on("mousedown", handleMouseDown);
    chart.getZr().on("mousemove", handleMouseMove);
    chart.getZr().on("mouseup", handleMouseUp);

    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(chartNode.current);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", resize);
      chart.getZr().off("mousedown", handleMouseDown);
      chart.getZr().off("mousemove", handleMouseMove);
      chart.getZr().off("mouseup", handleMouseUp);
      chart.off("datazoom", handleZoom);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (!payload || !displayed.length) {
      chart.clear();
      return;
    }
    const laneMode = Boolean(lanes);
    const laneCount = lanes?.labels.size ?? 0;

    // Single clean orange dashed line for selected incident
    const selectedIncident = incidentMarks.find((inc) => inc.id === selectedIncidentId);
    const incidentTimeVal = selectedIncident ? (selectedIncident.csv_sample_time ?? selectedIncident.start_time) : undefined;
    const selectedIncidentMark = typeof incidentTimeVal === "number" && incidentTimeVal >= payload.start && incidentTimeVal <= payload.end ? [{
      name: selectedIncident?.title ?? "오류 발생 지점",
      xAxis: incidentTimeVal,
      lineStyle: { color: "#ff7a00", width: 2, type: "dashed" as const },
      label: {
        show: true,
        position: "insideEndTop" as const,
        formatter: `⚠️ ${selectedIncident?.title ?? "오류 발생 지점"}`,
        color: "#ffffff",
        fontSize: 11,
        fontWeight: "bold" as const,
        backgroundColor: "rgba(211, 84, 0, 0.92)",
        padding: [3, 7],
        borderRadius: 3,
        borderColor: "#ff7a00",
        borderWidth: 1,
      },
    }] : [];

    const playbackMark = typeof cursorTime === "number" && cursorTime >= payload.start && cursorTime <= payload.end ? [{
      name: "재생 위치",
      xAxis: cursorTime,
      lineStyle: { color: "#f4c15d", width: 2, type: "solid" as const },
      label: { show: false },
    }] : [];

    const markLineData = [
      ...playbackMark,
      ...selectedIncidentMark,
    ];

    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      color: SERIES_COLORS,
      legend: {
        type: "scroll",
        textStyle: { color: "#c8d0d5", fontSize: 11 },
        top: 0,
        left: 0,
        right: 0,
        show: category !== "state" || laneCount <= 8,
      },
      grid: {
        left: category === "system" || category === "state" ? 190 : signalUnit ? 84 : 58,
        right: 24,
        top: 36,
        bottom: 50,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line" },
        formatter: category === "system"
          ? (params: SystemTooltipParam | SystemTooltipParam[]) => systemTooltip(params, displayed, payload.start)
          : (params: SystemTooltipParam | SystemTooltipParam[]) => signalTooltip(params, payload.start, signalUnit?.symbol),
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
        {
          type: "slider",
          start: zoomRange.start,
          end: zoomRange.end,
          bottom: 4,
          height: 20,
          zoomLock: false,
          brushSelect: false,
          showDataShadow: false,
          showDetail: false,
          handleSize: 14,
          moveHandleSize: 8,
          borderColor: "#394148",
          fillerColor: "rgba(101,200,179,.16)",
          textStyle: { color: "#aab4bc", fontSize: 11 },
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
        axisLabel: { color: "#c1c9ce", fontSize: 11, formatter: (value: number) => lanes.labels.get(Math.round(value)) ?? "" },
        splitLine: { lineStyle: { color: "#252b31" } },
      } : {
        type: "value",
        scale: true,
        name: signalUnit?.axisLabel,
        nameLocation: "middle",
        nameGap: 52,
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
    }, { notMerge: true, lazyUpdate: true });
  }, [category, cursorTime, displayed, incidentMarks, lanes, payload, selectedIncidentId, signalUnit, zoomRange]);

  return <section className={`csvPlot csvPlot-${kind === "primary" ? "primary" : "secondary csvPlot-comparison"}`} aria-label={categoryLabel}>
    <div className="csvChartHeader">
      <div>
        <h3>
          {categoryLabel}
          <span>{selectedNames.length}개 신호</span>
          {kind === "comparison" && <b className="plotPriority">비교 {comparisonIndex ?? 1}</b>}
        </h3>
      </div>
      {onRemove && <button type="button" className="textButton danger" aria-label={`비교 Plot 삭제: ${categoryLabel}`} onClick={onRemove}>삭제</button>}
    </div>

    <div
      className="csvTimeline"
      data-zoom-start={zoomRange.start.toFixed(3)}
      data-zoom-end={zoomRange.end.toFixed(3)}
      data-y-unit={signalUnit?.symbol ?? ""}
      data-y-scale={signalUnit?.scale ?? 1}
      style={{ height: lanes ? Math.min(1200, Math.max(260, lanes.labels.size * 30 + 120)) : undefined }}
      role="img"
      aria-label={`${kind === "comparison" ? "비교 " : ""}CSV ${categoryLabel} 그래프${signalUnit ? `, Y축 ${signalUnit.axisLabel}` : ""}: ${
        lanes ? [...lanes.labels.values()].join(", ") : selectedNames.join(", ")
      }`}
    >
      {loading && <div className="chartLoading">CSV 신호를 불러오는 중입니다.</div>}
      {error && <div className="chartLoading errorText">{error}</div>}
      {!loading && !error && !displayed.length && <div className="chartLoading">선택한 항목에 표시할 샘플이 없습니다.</div>}
      <div className={!loading && !error && displayed.length ? "csvChart" : "csvChart isHidden"} ref={chartNode} />
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
  const [category, setCategory] = useState<SignalCategory>("position");
  const [comparisonCategories, setComparisonCategories] = useState<SignalCategory[]>([]);
  const [comparisonCandidate, setComparisonCandidate] = useState<SignalCategory | "">("");
  const [selectedJointNames, setSelectedJointNames] = useState<string[]>(() => {
    try {
      const raw = window.sessionStorage.getItem("rby1_selected_joints_global");
      if (raw) return JSON.parse(raw);
    } catch {}
    return [];
  });
  const [zoomRange, setZoomRange] = useState<ZoomRange>({ start: 0, end: 100 });
  const [artifactPayloads, setArtifactPayloads] = useState<Record<number, CsvChartPayload>>({});
  const [fetchingArtifactId, setFetchingArtifactId] = useState<number | null>(null);
  const [artifactFetchError, setArtifactFetchError] = useState<string>("");

  // Incident selection for marking on plot
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null);

  // 3D Simulation view state & playback
  const [show3DView, setShow3DView] = useState(false);
  const [cursorTime, setCursorTime] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const cursorRef = useRef(cursorTime);

  useEffect(() => {
    cursorRef.current = cursorTime;
  }, [cursorTime]);

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
  const resolvedCategory = categories.some((item) => item.key === category) ? category : categories[0]?.key ?? "position";
  const resolvedComparisonCategories = useMemo(() => {
    const valid = new Set(categories.map((item) => item.key));
    const seen = new Set<SignalCategory>([resolvedCategory]);
    return comparisonCategories.filter((item) => {
      if (!valid.has(item) || seen.has(item)) return false;
      seen.add(item);
      return true;
    });
  }, [categories, comparisonCategories, resolvedCategory]);
  const plotCategories = useMemo(
    () => [resolvedCategory, ...resolvedComparisonCategories],
    [resolvedCategory, resolvedComparisonCategories],
  );
  const plotCategoryEntries = useMemo(() => plotCategories.map((item) => ({
    category: item,
    eligibleJoints: eligibleJointsFor(item, joints, available),
  })), [available, joints, plotCategories]);
  const selectorEligibleJoints = useMemo(
    () => sortJoints([...new Set(plotCategoryEntries.flatMap((item) => item.eligibleJoints))]),
    [plotCategoryEntries],
  );
  const resolvedSelectorJoints = useMemo(() => {
    if (!selectorEligibleJoints.length) return [];
    if (!selectedJointNames.length) return selectorEligibleJoints;
    const matched = selectorEligibleJoints.filter((j) => selectedJointNames.includes(j));
    return matched.length > 0 ? matched : selectorEligibleJoints;
  }, [selectedJointNames, selectorEligibleJoints]);
  const plots = useMemo(() => plotCategoryEntries.map((entry) => {
    const selected = entry.eligibleJoints.filter((joint) => resolvedSelectorJoints.includes(joint));
    const names = entry.category === "system"
      ? namesFor(entry.category, "", available)
      : selected.flatMap((joint) => namesFor(entry.category, joint, available));
    return { category: entry.category, selectedNames: [...new Set(names)] };
  }), [available, plotCategoryEntries, resolvedSelectorJoints]);

  const jointGroups = useMemo(() => groupJoints(selectorEligibleJoints), [selectorEligibleJoints]);
  const comparisonOptions = useMemo(() => categories.filter((item) => (
    item.key !== resolvedCategory && !resolvedComparisonCategories.includes(item.key)
  )), [categories, resolvedCategory, resolvedComparisonCategories]);
  const resolvedComparisonCandidate = comparisonOptions.some((item) => item.key === comparisonCandidate)
    ? comparisonCandidate
    : comparisonOptions[0]?.key ?? "";

  function toggleJoint(name: string, checked: boolean) {
    const next = checked
      ? selectorEligibleJoints.filter((joint) => joint === name || resolvedSelectorJoints.includes(joint))
      : resolvedSelectorJoints.filter((joint) => joint !== name);
    setSelectedJointNames(next);
  }

  function setSelectedJoints(next: string[]) {
    setSelectedJointNames(next);
  }

  function handleIncidentClick(inc: LinkedIncident) {
    setSelectedIncidentId((prev) => (prev === inc.id ? null : inc.id));
    const targetTime = inc.csv_sample_time ?? inc.start_time;
    if (typeof targetTime === "number") {
      setCursorTime(targetTime);
    }
  }

  useEffect(() => {
    if (!resolvedArtifactId) return;
    if (artifactPayloads[resolvedArtifactId]) return;
    let active = true;
    setFetchingArtifactId(resolvedArtifactId);
    setArtifactFetchError("");
    client.json<CsvChartPayload>(`/api/v3/cases/${caseId}/csvs/${resolvedArtifactId}/chart?max_points=2000&skip_dense=true`)
      .then((result) => {
        if (!active) return;
        setArtifactPayloads((prev) => ({ ...prev, [resolvedArtifactId]: result }));
        setCursorTime(result.start);
        setFetchingArtifactId(null);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setArtifactFetchError(reason instanceof Error ? reason.message : String(reason));
        setFetchingArtifactId(null);
      });
    return () => { active = false; };
  }, [caseId, client, resolvedArtifactId, artifactPayloads]);

  const payload = resolvedArtifactId ? artifactPayloads[resolvedArtifactId] ?? null : null;
  const chartLoading = Boolean(resolvedArtifactId && fetchingArtifactId === resolvedArtifactId && !payload);
  const chartError = artifactFetchError;
  const seriesByPlot = useMemo(() => plots.map((plot) => {
    const selected = new Set(plot.selectedNames);
    return payload?.series.filter((item) => selected.has(item.name)) ?? [];
  }), [payload, plots]);

  const activeIncidents: LinkedIncident[] = useMemo(() => {
    if (payload?.linked_incidents && payload.linked_incidents.length > 0) {
      return payload.linked_incidents;
    }
    if (csv?.linked_incidents && csv.linked_incidents.length > 0) {
      return csv.linked_incidents;
    }
    return [];
  }, [csv?.linked_incidents, payload?.linked_incidents]);

  // 3D Robot model & pose calculation
  const baseModel = normalizeModel(csv?.robot_model);
  const activeModel: RobotModelDescriptor = baseModel;

  const allPositionSeries = useMemo(() => {
    if (!payload) return [];
    return joints
      .map((joint) => payload.series.find((s) => s.name === `${joint}_pos`))
      .filter((item): item is CsvSeries => Boolean(item));
  }, [payload, joints]);

  const pose = useMemo(() => Object.fromEntries(allPositionSeries.map((item) => [
    item.name.slice(0, -"_pos".length),
    interpolate(item.points, cursorTime),
  ])), [allPositionSeries, cursorTime]);

  const playbackAvailable = Boolean(payload && allPositionSeries.length > 0);
  const start = payload?.start ?? csv?.min_sample_time ?? 0;
  const end = payload?.end ?? csv?.max_sample_time ?? start;
  const cursorLabel = `${formatDuration(cursorTime - start)} / ${formatDuration(end - start)}`;

  useEffect(() => {
    if (!playing || !payload || !playbackAvailable) return;
    let frame = 0;
    let previous = performance.now();
    let renderedAt = previous;
    const tick = (now: number) => {
      frame = window.requestAnimationFrame(tick);
      if (now - renderedAt < 32) return;
      const elapsed = Math.min((now - previous) / 1000, 0.1) * speed;
      previous = now;
      renderedAt = now;
      const next = cursorRef.current + elapsed;
      if (next >= payload.end) {
        setCursorTime(payload.end);
        setPlaying(false);
        window.cancelAnimationFrame(frame);
        return;
      }
      setCursorTime(next);
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
    {/* 1. Full-width Header */}
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

    {/* 2. Full-width Joint Selector */}
    {selectorEligibleJoints.length > 0 && (
      <section className="jointSelector" aria-labelledby="joint-selector-title">
        <div className="jointSelectorHead">
          <div><h3 id="joint-selector-title">조인트 선택</h3><span>{resolvedSelectorJoints.length} / {selectorEligibleJoints.length}개 선택</span></div>
        </div>
        <div className="jointGroupActions" role="group" aria-label="CSV 조인트 그룹 선택">
          {jointGroups.map((group) => {
            const groupSelected = group.joints.every((joint) => resolvedSelectorJoints.includes(joint));
            return (
              <button
                type="button"
                className={`textButton${groupSelected ? " active" : ""}`}
                aria-pressed={groupSelected}
                aria-label={`CSV ${group.label} 그룹 선택 전환`}
                onClick={() => {
                  const groupSet = new Set(group.joints);
                  setSelectedJoints(groupSelected
                    ? resolvedSelectorJoints.filter((joint) => !groupSet.has(joint))
                    : selectorEligibleJoints.filter((joint) => groupSet.has(joint) || resolvedSelectorJoints.includes(joint)));
                }}
                key={group.key}
              >{group.label}</button>
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

    {/* 3. Section Detected Errors Banner (Placed below Joint Selector) */}
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

    {/* 4. Plot & Simulation Workspace */}
    <div className={`csvWorkspaceSplit ${show3DView ? "splitView" : "fullWidth"}`}>
      {/* Left Area: Vertical Signal Category Tab Bar attached directly to the left of 2D Plot */}
      <div className="plotsWithCategoryLayout">
        <nav className="plotCategoryNav" aria-label="CSV 신호 분류">
          {/* 3D View Toggle Button placed directly above the signal category box */}
          <div className="plotCategory3DTopToggle">
            <button
              type="button"
              className={`btnToggle3D ${show3DView ? "active" : ""}`}
              onClick={() => setShow3DView((prev) => !prev)}
              title="3D 로봇 자세 시뮬레이터 및 재생 컨트롤을 열고 닫습니다"
            >
              <span className="btn3DIcon">🤖</span>
              <span className="btn3DText">3D 시각화 {show3DView ? "ON" : "OFF"}</span>
            </button>
          </div>

          <div className="plotCategoryNavHeader">
            <span>신호 분류</span>
          </div>
          <div className="plotCategoryNavList">
            {categories.map((item) => {
              const isActive = item.key === resolvedCategory;
              return (
                <button
                  type="button"
                  className={`plotCategoryNavBtn ${isActive ? "active" : ""}`}
                  key={item.key}
                  onClick={() => {
                    setCategory(item.key);
                    setComparisonCategories((current) => current.filter((candidate) => candidate !== item.key));
                  }}
                >
                  <strong>{item.label}</strong>
                  <small>{item.description}</small>
                </button>
              );
            })}
          </div>

          <div className="plotComparisonNavSection">
            <span className="comparisonNavTitle">비교 Plot</span>
            <select
              aria-label="비교 Plot에 추가할 신호"
              value={resolvedComparisonCandidate}
              disabled={!comparisonOptions.length}
              onChange={(event) => setComparisonCandidate(event.target.value as SignalCategory | "")}
            >
              {!comparisonOptions.length && <option value="">추가 불가</option>}
              {comparisonOptions.map((item) => <option value={item.key} key={item.key}>{item.label}</option>)}
            </select>
            <button
              type="button"
              className="textButton addComparisonBtn"
              disabled={!resolvedComparisonCandidate}
              onClick={() => {
                if (!resolvedComparisonCandidate) return;
                setComparisonCategories((current) => [...current, resolvedComparisonCandidate]);
              }}
            >+ 추가</button>
          </div>
        </nav>

        {/* 2D Plots Box */}
        <div className="csvPlotsContainer">
          <div className={`csvPlotsBox ${plots.length > 1 ? "hasMultiplePlots" : ""}`}>
            {plots[0] && <CsvPlot
              category={plots[0].category}
              selectedNames={plots[0].selectedNames}
              payload={payload}
              loading={chartLoading}
              error={chartError}
              kind="primary"
              zoomRange={zoomRange}
              onZoomRangeChange={setZoomRange}
              incidentMarks={activeIncidents}
              selectedIncidentId={selectedIncidentId}
              cursorTime={show3DView ? cursorTime : undefined}
              onCursorChange={setCursorTime}
            />}

            {plots.slice(1).map((plot, index) => <CsvPlot
              category={plot.category}
              selectedNames={plot.selectedNames}
              payload={payload}
              loading={chartLoading}
              error={chartError}
              kind="comparison"
              comparisonIndex={index + 1}
              onRemove={() => setComparisonCategories((current) => current.filter((item) => item !== plot.category))}
              zoomRange={zoomRange}
              onZoomRangeChange={setZoomRange}
              incidentMarks={activeIncidents}
              selectedIncidentId={selectedIncidentId}
              cursorTime={show3DView ? cursorTime : undefined}
              onCursorChange={setCursorTime}
              key={plot.category}
            />)}
          </div>

          <div className="csvPlotSummaries">
            {plots.map((plot, index) => <StateSummary
              category={plot.category}
              series={seriesByPlot[index] ?? []}
              definitions={payload?.motor_state_bits ?? []}
              contract={payload?.motor_state_contract}
              systemContract={payload?.system_state_contract}
              key={plot.category}
            />)}
          </div>
        </div>
      </div>

      {/* Right Area: 3D Simulation (Only when show3DView is true) */}
      {show3DView && (
        <div className="simulationColumn">
          <div className="simulationDockSticky">
            <section className="simulationDock" aria-label="3D 로봇 자세 시뮬레이션">
              <div className="simViewerContainer">
                <RobotViewer
                  model={activeModel}
                  jointValues={pose}
                  cursorLabel={cursorLabel}
                />
              </div>
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
            </section>
          </div>
        </div>
      )}
    </div>
  </section>;
}

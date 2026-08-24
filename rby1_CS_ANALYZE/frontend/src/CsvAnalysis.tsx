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

type CsvSeriesMeta = { name: string; kind: "continuous" | "discrete" };
type CsvArtifact = {
  id: number;
  name: string;
  member?: string;
  min_sample_time?: number;
  max_sample_time?: number;
  sample_count: number;
  available_series: CsvSeriesMeta[];
  detected_joints: string[];
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
};
type SignalCategory = "position" | "velocity" | "current" | "torque" | "state" | "gain" | "system";
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
const SELECTED_JOINTS_STORAGE_KEY = "rby1-csv-selected-joints-v2";

function loadSelectedJoints(): Record<string, string[]> {
  try {
    const raw = window.sessionStorage.getItem(SELECTED_JOINTS_STORAGE_KEY);
    if (raw === null) return {};
    const stored = JSON.parse(raw);
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return {};
    return Object.fromEntries(Object.entries(stored).flatMap(([key, value]) => (
      Array.isArray(value)
        ? [[key, value.filter((item): item is string => typeof item === "string")]]
        : []
    )));
  } catch {
    return {};
  }
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
  ];
  const suffix = suffixes.find((item) => name.endsWith(item));
  return suffix ? name.slice(0, -suffix.length) : null;
}

function namesFor(category: SignalCategory, joint: string, available: Set<string>): string[] {
  const candidates: Record<Exclude<SignalCategory, "system">, string[]> = {
    position: [`${joint}_pos`, `${joint}_target_pos`],
    velocity: [`${joint}_vel`, `${joint}_target_vel`],
    current: [`${joint}_cur`],
    torque: [`${joint}_tq`, `${joint}_target_ff_tq`],
    state: [`${joint}_state`],
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

function selectedJointsFor(eligibleJoints: string[], selectedJoints?: string[]): string[] {
  if (selectedJoints === undefined) return eligibleJoints.slice(0, 1);
  const valid = eligibleJoints.filter((name) => selectedJoints.includes(name));
  return valid.length || selectedJoints.length === 0 ? valid : eligibleJoints.slice(0, 1);
}

function formatDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "--";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  return `${seconds.toFixed(seconds < 10 ? 3 : 1)} s`;
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
}) {
  const chartNode = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof init> | null>(null);
  const zoomCallbackRef = useRef(onZoomRangeChange);
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
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
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
    const laneCount = lanes?.labels.size ?? 0;
    const laneMode = category === "state" || category === "system";
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
        top: 44,
        bottom: 62,
      },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "line" },
        formatter: category === "system"
          ? (params: SystemTooltipParam | SystemTooltipParam[]) => systemTooltip(params, displayed, payload.start)
          : (params: SystemTooltipParam | SystemTooltipParam[]) => signalTooltip(params, payload.start, signalUnit?.symbol),
      },
      dataZoom: [
        { type: "inside", filterMode: "none", start: zoomRange.start, end: zoomRange.end },
        {
          type: "slider",
          start: zoomRange.start,
          end: zoomRange.end,
          bottom: 8,
          height: 22,
          zoomLock: true,
          brushSelect: false,
          showDataShadow: false,
          showDetail: false,
          handleSize: 0,
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
        markLine: index === 0 ? {
          silent: true,
          symbol: ["none", "none"],
          label: { formatter: "CSV 종료", color: "#f0b85b", fontSize: 11 },
          lineStyle: { color: "#f0b85b", width: 1.2, type: "dashed" },
          data: [{ xAxis: payload.end }],
        } : undefined,
      })),
    }, { notMerge: true, lazyUpdate: true });
  }, [category, displayed, lanes, payload, signalUnit, zoomRange]);

  const plotLabel = kind === "primary" ? "기본 Plot" : `비교 Plot ${comparisonIndex ?? 1}`;

  return <section className={`csvPlot csvPlot-${kind === "primary" ? "primary" : "secondary csvPlot-comparison"}`} aria-label={plotLabel}>
    <div className="csvChartHeader">
      <div>
        <h3><b className="plotPriority">{plotLabel}</b>{categoryLabel}<span>{selectedNames.length}개 신호</span></h3>
        <p className="selectedSeriesText">{selectedNames.join(" · ") || "선택 가능한 신호가 없습니다."}</p>
      </div>
      {onRemove && <button type="button" className="textButton danger" aria-label={`비교 Plot 삭제: ${categoryLabel}`} onClick={onRemove}>삭제</button>}
    </div>

    <div
      className="csvTimeline"
      data-zoom-start={zoomRange.start.toFixed(3)}
      data-zoom-end={zoomRange.end.toFixed(3)}
      data-y-unit={signalUnit?.symbol ?? ""}
      data-y-scale={signalUnit?.scale ?? 1}
      style={{ height: lanes ? Math.min(1200, Math.max(440, lanes.labels.size * 30 + 120)) : undefined }}
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

export function CsvAnalysis({ client, caseId }: { client: ApiClient; caseId: string }) {
  const [listResult, setListResult] = useState<{ caseId: string; csvs: CsvArtifact[]; error?: string } | null>(null);
  const [artifactId, setArtifactId] = useState(0);
  const [category, setCategory] = useState<SignalCategory>("position");
  const [comparisonCategories, setComparisonCategories] = useState<SignalCategory[]>([]);
  const [comparisonCandidate, setComparisonCandidate] = useState<SignalCategory | "">("");
  const [selectedJointsByArtifact, setSelectedJointsByArtifact] = useState<Record<string, string[]>>(loadSelectedJoints);
  const [zoomRange, setZoomRange] = useState<ZoomRange>({ start: 0, end: 100 });
  const [chartResult, setChartResult] = useState<{
    requestKey: string;
    payload: CsvChartPayload | null;
    error?: string;
  } | null>(null);

  useEffect(() => {
    let active = true;
    client.json<CsvListPayload>(`/api/v3/cases/${caseId}/csvs`)
      .then((result) => {
        if (!active) return;
        setListResult({ caseId, csvs: result.csvs });
        setArtifactId(result.csvs[0]?.id ?? 0);
        setZoomRange({ start: 0, end: 100 });
      })
      .catch((reason: unknown) => {
        if (active) setListResult({ caseId, csvs: [], error: reason instanceof Error ? reason.message : String(reason) });
      });
    return () => { active = false; };
  }, [caseId, client]);

  useEffect(() => {
    try {
      window.sessionStorage.setItem(SELECTED_JOINTS_STORAGE_KEY, JSON.stringify(selectedJointsByArtifact));
    } catch {
      void 0;
    }
  }, [selectedJointsByArtifact]);

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
  const selectionKey = `${caseId}:${resolvedArtifactId}`;
  const storedSelection = selectedJointsByArtifact[selectionKey];
  const resolvedSelectorJoints = useMemo(
    () => selectedJointsFor(selectorEligibleJoints, storedSelection),
    [selectorEligibleJoints, storedSelection],
  );
  const plots = useMemo(() => plotCategoryEntries.map((entry) => {
    const selected = entry.eligibleJoints.filter((joint) => resolvedSelectorJoints.includes(joint));
    const names = entry.category === "system"
      ? namesFor(entry.category, "", available)
      : selected.flatMap((joint) => namesFor(entry.category, joint, available));
    return { category: entry.category, selectedNames: [...new Set(names)] };
  }), [available, plotCategoryEntries, resolvedSelectorJoints]);
  const requestedNames = useMemo(
    () => [...new Set(plots.flatMap((plot) => plot.selectedNames))],
    [plots],
  );
  const requestKey = `${caseId}:${resolvedArtifactId}:${requestedNames.join("|")}`;
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
    setSelectedJointsByArtifact((current) => ({ ...current, [selectionKey]: next }));
  }

  function setSelectedJoints(next: string[]) {
    setSelectedJointsByArtifact((current) => ({ ...current, [selectionKey]: next }));
  }

  useEffect(() => {
    if (!resolvedArtifactId || !requestedNames.length) return;
    let active = true;
    const params = new URLSearchParams({ max_points: "2000", skip_dense: "true" });
    requestedNames.forEach((name) => params.append("series", name));
    client.json<CsvChartPayload>(`/api/v3/cases/${caseId}/csvs/${resolvedArtifactId}/chart?${params}`)
      .then((result) => { if (active) setChartResult({ requestKey, payload: result }); })
      .catch((reason: unknown) => {
        if (!active) return;
        setChartResult({ requestKey, payload: null, error: reason instanceof Error ? reason.message : String(reason) });
    });
    return () => { active = false; };
  }, [caseId, client, requestKey, requestedNames, resolvedArtifactId]);

  const payload = chartResult?.requestKey === requestKey ? chartResult.payload : null;
  const chartLoading = Boolean(resolvedArtifactId && requestedNames.length && chartResult?.requestKey !== requestKey);
  const chartError = chartResult?.requestKey === requestKey ? chartResult.error ?? "" : "";
  const seriesByPlot = useMemo(() => plots.map((plot) => {
    const selected = new Set(plot.selectedNames);
    return payload?.series.filter((item) => selected.has(item.name)) ?? [];
  }), [payload, plots]);

  if (listLoading) return <section className="csvWorkspace"><div className="csvEmpty">CSV 목록을 불러오는 중입니다.</div></section>;
  if (listError) return <section className="csvWorkspace"><div className="csvEmpty errorText">{listError}</div></section>;
  if (!csvs.length) return <section className="csvWorkspace"><div className="csvEmpty"><strong>분석할 Fault CSV가 없습니다.</strong><span>상단의 파일 가져오기 또는 드래그앤드롭으로 CSV를 추가하십시오.</span></div></section>;

  return <section className="csvWorkspace" aria-label="Fault CSV 독립 분석">
    <header className="csvWorkspaceHeader">
      <div><h2>CSV 전체 신호 분석</h2><p>사건 연결 여부와 관계없이 Fault CSV의 전체 시간 구간을 조회합니다.</p></div>
      <label><span>CSV 파일</span><select aria-label="분석할 CSV 파일" value={resolvedArtifactId} onChange={(event) => {
        setArtifactId(Number(event.target.value));
        setZoomRange({ start: 0, end: 100 });
      }}>
        {csvs.map((item) => <option value={item.id} key={item.id}>{item.member || item.name}</option>)}
      </select></label>
    </header>

    {selectorEligibleJoints.length > 0 && <section className="jointSelector" aria-labelledby="joint-selector-title">
      <div className="jointSelectorHead">
        <div><h3 id="joint-selector-title">조인트 선택</h3><span>{resolvedSelectorJoints.length} / {selectorEligibleJoints.length}개 선택</span></div>
        <div>
          <button type="button" className="textButton" onClick={() => setSelectedJoints(selectorEligibleJoints)}>전체 선택</button>
          <button type="button" className="textButton" onClick={() => setSelectedJoints(selectorEligibleJoints.slice(0, 1))}>첫 조인트만</button>
          <button type="button" className="textButton" onClick={() => setSelectedJoints([])}>선택 해제</button>
        </div>
      </div>
      <div className="jointGroupActions" role="group" aria-label="CSV 조인트 그룹 선택">
        {jointGroups.map((group) => {
          const groupSelected = group.joints.every((joint) => resolvedSelectorJoints.includes(joint));
          return <button
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
          >{group.label}</button>;
        })}
      </div>
      <div className="jointGroupList">
        {jointGroups.map((group) => <section className={`jointGroupBlock jointGroup-${group.key}`} aria-label={`${group.label} 조인트`} key={group.key}>
          <h4>{group.label}<span>{group.joints.length}</span></h4>
          <div className="jointChecklist">
            {group.joints.map((item) => <label key={item}>
              <input
                type="checkbox"
                checked={resolvedSelectorJoints.includes(item)}
                onChange={(event) => toggleJoint(item, event.currentTarget.checked)}
              />
              <span>{item}</span>
            </label>)}
          </div>
        </section>)}
      </div>
    </section>}

    <div className="csvStats">
      <div><span>샘플</span><strong>{(csv?.sample_count ?? 0).toLocaleString()}</strong></div>
      <div><span>기록 구간</span><strong>{formatDuration((csv?.max_sample_time ?? 0) - (csv?.min_sample_time ?? 0))}</strong></div>
      <div className="csvSourceName"><span>원본</span><strong>{csv?.member || csv?.name}</strong></div>
    </div>

    <nav className="signalCategoryTabs" aria-label="CSV 신호 분류">
      {categories.map((item) => <button className={item.key === resolvedCategory ? "active" : ""} key={item.key} onClick={() => {
        setCategory(item.key);
        setComparisonCategories((current) => current.filter((candidate) => candidate !== item.key));
      }}>
        <strong>{item.label}</strong><span>{item.description}</span>
      </button>)}
    </nav>

    {plots[0] && <CsvPlot
      category={plots[0].category}
      selectedNames={plots[0].selectedNames}
      payload={payload}
      loading={chartLoading}
      error={chartError}
      kind="primary"
      zoomRange={zoomRange}
      onZoomRangeChange={setZoomRange}
    />}

    <section className="secondaryPlotControl" aria-labelledby="secondary-plot-title">
      <div>
        <h3 id="secondary-plot-title">비교 Plot</h3>
        <p>필요한 신호 분류를 여러 개 추가하고 같은 시간 구간에서 비교합니다.</p>
      </div>
      <div className="comparisonPlotActions">
        <label><span>추가할 신호</span><select
          aria-label="비교 Plot에 추가할 신호"
          value={resolvedComparisonCandidate}
          disabled={!comparisonOptions.length}
          onChange={(event) => setComparisonCandidate(event.target.value as SignalCategory | "")}
        >
          {!comparisonOptions.length && <option value="">추가 가능한 분류 없음</option>}
          {comparisonOptions.map((item) => <option value={item.key} key={item.key}>{item.label} · {item.description}</option>)}
        </select></label>
        <button
          type="button"
          className="textButton"
          disabled={!resolvedComparisonCandidate}
          onClick={() => {
            if (!resolvedComparisonCandidate) return;
            setComparisonCategories((current) => [...current, resolvedComparisonCandidate]);
          }}
        >Plot 추가</button>
      </div>
    </section>

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
      key={plot.category}
    />)}

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
  </section>;
}

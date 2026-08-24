import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
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
import { csvSignalDisplayValue } from "./csvSignalUnits";
import { groupJoints, isExactJointSelection, sortJoints } from "./jointGroups";
import { RobotViewer, type RobotModelDescriptor } from "./RobotViewer";

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
  robot_model?: RobotModelDescriptor;
};
type CsvListPayload = { csvs: CsvArtifact[] };
type CsvSeries = { name: string; kind: "continuous" | "discrete"; nan_count: number; points: [number, number][] };
type CsvChartPayload = {
  start: number;
  end: number;
  dense_series?: { name: string; required_points: number; suggested_window_seconds: number }[];
  series: CsvSeries[];
};
type ZoomRange = { start: number; end: number };
type LowerMetric = "current" | "torque";
type TooltipParam = {
  axisValue: number | string;
  marker: string;
  seriesName: string;
  value?: number | [number, number];
};

registerECharts([
  LineChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkLineComponent,
  TooltipComponent,
  CanvasRenderer,
]);

const COLORS = ["#65c8b3", "#f0b85b", "#e66e73", "#6ea8df", "#d7dc82", "#b9a0d8", "#8dd2eb"];

function escapeHtml(value: string): string {
  return value.replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "\"": "&quot;",
    "'": "&#39;",
  })[character] ?? character);
}

function formatAxisTime(value: number, start: number): string {
  if (value > 1_000_000_000) {
    const date = new Date(value * 1000);
    return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}:${String(date.getSeconds()).padStart(2, "0")}.${String(date.getMilliseconds()).padStart(3, "0")}`;
  }
  return `${(value - start).toFixed(3)}s`;
}

function formatDuration(value: number): string {
  const seconds = Math.max(value, 0);
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  const prefix = hours > 0 ? `${String(hours).padStart(2, "0")}:` : "";
  return `${prefix}${String(minutes).padStart(2, "0")}:${remainder.toFixed(3).padStart(6, "0")}`;
}

function chartTooltip(value: TooltipParam | TooltipParam[], start: number, unit: string): string {
  const params = Array.isArray(value) ? value : [value];
  const timestamp = Number(params[0]?.axisValue ?? start);
  const rows = params.map((item) => {
    const raw = Array.isArray(item.value) ? item.value[1] : item.value;
    const shown = typeof raw === "number" && Number.isFinite(raw) ? raw.toFixed(4) : "-";
    return `${item.marker}${escapeHtml(item.seriesName)}: ${shown} ${unit}`;
  });
  return [`<strong>${escapeHtml(formatAxisTime(timestamp, start))}</strong>`, ...rows].join("<br/>");
}

function initialZoom(start: number, end: number): ZoomRange {
  const duration = end - start;
  if (duration <= 30) return { start: 0, end: 100 };
  return { start: 0, end: Math.max(2, Math.min(100, 30 / duration * 100)) };
}

function playbackCursorMarkLine(cursorTime: number) {
  return {
    animation: false,
    silent: true,
    symbol: ["none", "none"],
    label: { show: false },
    lineStyle: { color: "#f4c15d", width: 2, type: "solid", opacity: 1 },
    data: [{ xAxis: cursorTime }],
  };
}

function normalizeModel(value?: RobotModelDescriptor): RobotModelDescriptor {
  const supportedVersion = value?.version === "v1.0" || value?.version === "v1.1" || value?.version === "v1.2" || value?.version === "v1.3"
    ? value.version
    : "v1.2";
  return {
    model: value?.model === "m" ? "m" : "a",
    version: supportedVersion,
    confidence: value?.confidence ?? "assumed",
    reason: value?.reason ?? "모델 정보가 없어 A Type 정밀구동 헤드 V1.2로 가정",
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

function VisualizationPlot({
  title,
  subtitle,
  unit,
  category,
  series,
  start,
  end,
  cursorTime,
  zoomRange,
  onCursorTimeChange,
  onZoomRangeChange,
  actions,
}: {
  title: string;
  subtitle: string;
  unit: "deg" | "A" | "Nm";
  category: "position" | LowerMetric;
  series: CsvSeries[];
  start: number;
  end: number;
  cursorTime: number;
  zoomRange: ZoomRange;
  onCursorTimeChange: (time: number) => void;
  onZoomRangeChange: (range: ZoomRange) => void;
  actions?: ReactNode;
}) {
  const nodeRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<ReturnType<typeof init> | null>(null);
  const cursorCallback = useRef(onCursorTimeChange);
  const zoomCallback = useRef(onZoomRangeChange);
  const cursorValueRef = useRef(cursorTime);
  const displayed = useMemo(() => series.map((item) => ({
    ...item,
    points: item.points.map(([time, value]): [number, number] => [
      time,
      category === "position" ? csvSignalDisplayValue("position", value) : value,
    ]),
  })), [category, series]);
  const cursorSeriesId = displayed[0]?.name ?? "";

  useEffect(() => { cursorCallback.current = onCursorTimeChange; }, [onCursorTimeChange]);
  useEffect(() => { zoomCallback.current = onZoomRangeChange; }, [onZoomRangeChange]);
  useEffect(() => { cursorValueRef.current = cursorTime; }, [cursorTime]);

  useEffect(() => {
    if (!nodeRef.current) return;
    const chart = init(nodeRef.current);
    chartRef.current = chart;
    const handlePointer = (event: unknown) => {
      const axis = (event as { axesInfo?: { value?: number }[] }).axesInfo?.[0];
      if (typeof axis?.value === "number") cursorCallback.current(axis.value);
    };
    const handleZoom = (event: unknown) => {
      const value = event as { start?: number; end?: number; batch?: { start?: number; end?: number }[] };
      const range = value.batch?.[0] ?? value;
      if (typeof range.start === "number" && typeof range.end === "number") {
        zoomCallback.current({ start: range.start, end: range.end });
      }
    };
    chart.on("updateAxisPointer", handlePointer);
    chart.on("datazoom", handleZoom);
    const resize = new ResizeObserver(() => chart.resize());
    resize.observe(nodeRef.current);
    return () => {
      resize.disconnect();
      chart.off("updateAxisPointer", handlePointer);
      chart.off("datazoom", handleZoom);
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    if (!displayed.length || end <= start) {
      chart.clear();
      return;
    }
    chart.setOption({
      animation: false,
      backgroundColor: "transparent",
      color: COLORS,
      legend: {
        type: "scroll",
        top: 1,
        left: 4,
        right: 4,
        textStyle: { color: "#c8d0d5", fontSize: 10 },
      },
      grid: { left: 68, right: 18, top: 42, bottom: 62 },
      tooltip: {
        trigger: "axis",
        triggerOn: "mousemove|click",
        axisPointer: { type: "line", lineStyle: { color: "#e8ecef", width: 1 } },
        formatter: (params: TooltipParam | TooltipParam[]) => chartTooltip(params, start, unit),
      },
      dataZoom: [
        {
          type: "inside",
          filterMode: "none",
          start: zoomRange.start,
          end: zoomRange.end,
          zoomOnMouseWheel: true,
          moveOnMouseWheel: true,
          moveOnMouseMove: true,
        },
        {
          type: "slider",
          filterMode: "none",
          start: zoomRange.start,
          end: zoomRange.end,
          bottom: 10,
          height: 20,
          brushSelect: false,
          showDataShadow: false,
          showDetail: false,
          borderColor: "#394148",
          fillerColor: "rgba(101,200,179,.18)",
          handleStyle: { color: "#65c8b3", borderColor: "#65c8b3" },
          moveHandleStyle: { color: "#65c8b3", opacity: 0.65 },
        },
      ],
      xAxis: {
        type: "value",
        min: start,
        max: end,
        axisPointer: { show: true, value: cursorValueRef.current, snap: false, label: { show: false } },
        axisLabel: { color: "#aab4bc", fontSize: 10, formatter: (value: number) => formatAxisTime(value, start) },
        splitLine: { lineStyle: { color: "#252b31" } },
      },
      yAxis: {
        type: "value",
        scale: true,
        name: unit,
        nameLocation: "middle",
        nameGap: 46,
        nameTextStyle: { color: "#d1d7db", fontSize: 11, fontWeight: 700 },
        axisLabel: { color: "#aab4bc", fontSize: 10 },
        splitLine: { lineStyle: { color: "#252b31" } },
      },
      series: displayed.map((item, index) => ({
        id: item.name,
        name: item.name,
        type: "line",
        showSymbol: false,
        sampling: false,
        lineStyle: { width: 1.6 },
        data: item.points,
        markLine: index === 0 ? playbackCursorMarkLine(cursorValueRef.current) : undefined,
      })),
    }, { notMerge: true, lazyUpdate: true });
  }, [displayed, end, start, unit, zoomRange]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !cursorSeriesId) return;
    chart.setOption({
      series: [{
        id: cursorSeriesId,
        markLine: playbackCursorMarkLine(cursorTime),
      }],
    }, { lazyUpdate: false });
    chart.dispatchAction({ type: "updateAxisPointer", xAxisIndex: 0, value: cursorTime });
  }, [cursorSeriesId, cursorTime]);

  return <section className="visualizationPlot" aria-label={`${title} 그래프`}>
    <header>
      <div><h3>{title}<span>{series.length}개 신호</span></h3><p>{subtitle}</p></div>
      {actions}
    </header>
    <div
      className="visualizationPlotBody"
      data-zoom-start={zoomRange.start.toFixed(3)}
      data-zoom-end={zoomRange.end.toFixed(3)}
      data-unit={unit}
      data-cursor-time={cursorTime.toFixed(6)}
      data-cursor-visible={displayed.length ? "true" : "false"}
    >
      {!displayed.length && <div className="chartLoading">선택한 조인트에 표시할 신호가 없습니다.</div>}
      <div className={displayed.length ? "visualizationChart" : "visualizationChart isHidden"} ref={nodeRef} />
    </div>
  </section>;
}

export function RobotVisualization({ client, caseId }: { client: ApiClient; caseId: string }) {
  const [listResult, setListResult] = useState<{ caseId: string; csvs: CsvArtifact[]; error?: string } | null>(null);
  const [artifactId, setArtifactId] = useState(0);
  const [selectionByArtifact, setSelectionByArtifact] = useState<Record<number, string[]>>({});
  const [metric, setMetric] = useState<LowerMetric>("current");
  const [zoomRange, setZoomRange] = useState<ZoomRange>({ start: 0, end: 100 });
  const [cursorTime, setCursorTime] = useState(0);
  const [speed, setSpeed] = useState(1);
  const [playing, setPlaying] = useState(false);
  const [chartResult, setChartResult] = useState<{ requestKey: string; payload: CsvChartPayload | null; error?: string } | null>(null);
  const cursorRef = useRef(cursorTime);

  useEffect(() => { cursorRef.current = cursorTime; }, [cursorTime]);

  useEffect(() => {
    let active = true;
    client.json<CsvListPayload>(`/api/v3/cases/${caseId}/csvs`)
      .then((result) => {
        if (!active) return;
        setListResult({ caseId, csvs: result.csvs });
        setArtifactId(result.csvs[0]?.id ?? 0);
      })
      .catch((reason: unknown) => {
        if (active) setListResult({ caseId, csvs: [], error: reason instanceof Error ? reason.message : String(reason) });
      });
    return () => { active = false; };
  }, [caseId, client]);

  const csvs = listResult?.caseId === caseId ? listResult.csvs : [];
  const listLoading = listResult?.caseId !== caseId;
  const listError = listResult?.caseId === caseId ? listResult.error ?? "" : "";
  const resolvedArtifactId = csvs.some((item) => item.id === artifactId) ? artifactId : csvs[0]?.id ?? 0;
  const csv = csvs.find((item) => item.id === resolvedArtifactId) ?? null;
  const available = useMemo(() => new Set(csv?.available_series.map((item) => item.name) ?? []), [csv]);
  const joints = useMemo(
    () => (csv?.detected_joints ?? []).filter((joint) => available.has(`${joint}_pos`)),
    [available, csv],
  );
  const sortedJoints = useMemo(() => sortJoints(joints), [joints]);
  const jointGroups = useMemo(() => groupJoints(sortedJoints), [sortedJoints]);
  const storedSelection = selectionByArtifact[resolvedArtifactId];
  const selectedJoints = useMemo(() => {
    if (!csv) return [];
    if (storedSelection === undefined) return sortedJoints;
    return sortedJoints.filter((joint) => storedSelection.includes(joint));
  }, [csv, sortedJoints, storedSelection]);
  const positionNames = useMemo(() => sortedJoints.map((joint) => `${joint}_pos`).filter((name) => available.has(name)), [available, sortedJoints]);
  const currentNames = useMemo(() => sortedJoints.map((joint) => `${joint}_cur`).filter((name) => available.has(name)), [available, sortedJoints]);
  const torqueNames = useMemo(() => sortedJoints.flatMap((joint) => [`${joint}_tq`, `${joint}_target_ff_tq`]).filter((name) => available.has(name)), [available, sortedJoints]);
  const requestedNames = useMemo(() => [...new Set([...positionNames, ...currentNames, ...torqueNames])], [currentNames, positionNames, torqueNames]);
  const requestKey = `${caseId}:${resolvedArtifactId}:${requestedNames.join("|")}`;

  useEffect(() => {
    if (!resolvedArtifactId || !requestedNames.length) return;
    let active = true;
    const params = new URLSearchParams({ max_points: "2000", skip_dense: "true" });
    requestedNames.forEach((name) => params.append("series", name));
    client.json<CsvChartPayload>(`/api/v3/cases/${caseId}/csvs/${resolvedArtifactId}/chart?${params}`)
      .then((payload) => {
        if (!active) return;
        setChartResult({ requestKey, payload });
        setCursorTime(payload.start);
        setZoomRange(initialZoom(payload.start, payload.end));
        setPlaying(false);
      })
      .catch((reason: unknown) => {
        if (!active) return;
        setChartResult({ requestKey, payload: null, error: reason instanceof Error ? reason.message : String(reason) });
      });
    return () => { active = false; };
  }, [caseId, client, requestKey, requestedNames, resolvedArtifactId]);

  const payload = chartResult?.requestKey === requestKey ? chartResult.payload : null;
  const chartLoading = Boolean(resolvedArtifactId && requestedNames.length && chartResult?.requestKey !== requestKey);
  const chartError = chartResult?.requestKey === requestKey ? chartResult.error ?? "" : "";
  const seriesByName = useMemo(() => new Map(payload?.series.map((item) => [item.name, item]) ?? []), [payload]);
  const selectedPositionSeries = useMemo(() => selectedJoints
    .map((joint) => seriesByName.get(`${joint}_pos`))
    .filter((item): item is CsvSeries => Boolean(item)), [selectedJoints, seriesByName]);
  const currentAvailable = currentNames.length > 0;
  const torqueAvailable = torqueNames.length > 0;
  const resolvedMetric: LowerMetric = metric === "current" && !currentAvailable && torqueAvailable ? "torque" : metric;
  const lowerSeries = useMemo(() => {
    const suffixes = resolvedMetric === "current" ? ["_cur"] : ["_tq", "_target_ff_tq"];
    return selectedJoints.flatMap((joint) => suffixes
      .map((suffix) => seriesByName.get(`${joint}${suffix}`))
      .filter((item): item is CsvSeries => Boolean(item)));
  }, [resolvedMetric, selectedJoints, seriesByName]);
  const pose = useMemo(() => Object.fromEntries(selectedPositionSeries.map((item) => [
    item.name.slice(0, -"_pos".length),
    interpolate(item.points, cursorTime),
  ])), [cursorTime, selectedPositionSeries]);
  const playbackAvailable = Boolean(payload && selectedPositionSeries.length > 0);

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

  function setSelection(next: string[]) {
    if (!resolvedArtifactId) return;
    setPlaying(false);
    setSelectionByArtifact((current) => ({ ...current, [resolvedArtifactId]: next }));
  }

  function toggleJoint(joint: string, checked: boolean) {
    setSelection(checked
      ? sortedJoints.filter((candidate) => candidate === joint || selectedJoints.includes(candidate))
      : selectedJoints.filter((candidate) => candidate !== joint));
  }

  function startPlayback() {
    if (!payload || !playbackAvailable) return;
    if (cursorRef.current >= payload.end) setCursorTime(payload.start);
    setPlaying(true);
  }

  if (listLoading) return <section className="visualizationWorkspace"><div className="csvEmpty">CSV 목록을 불러오는 중입니다.</div></section>;
  if (listError) return <section className="visualizationWorkspace"><div className="csvEmpty errorText">{listError}</div></section>;
  if (!csvs.length) return <section className="visualizationWorkspace"><div className="csvEmpty"><strong>시각화할 Fault CSV가 없습니다.</strong><span>상단의 파일 가져오기 또는 드래그앤드롭으로 CSV를 추가하십시오.</span></div></section>;

  const start = payload?.start ?? csv?.min_sample_time ?? 0;
  const end = payload?.end ?? csv?.max_sample_time ?? start;
  const cursorLabel = `${formatDuration(cursorTime - start)} / ${formatDuration(end - start)}`;
  const model = normalizeModel(csv?.robot_model);

  return <section className="visualizationWorkspace" aria-label="로봇 자세 및 CSV 시각화">
    <header className="visualizationHeader">
      <div><h2>로봇 자세 시각화</h2><p>CSV의 관절 위치와 3D 자세를 동일한 시간 커서로 확인합니다.</p></div>
      <div className="visualizationHeaderControls">
        <label><span>CSV 파일</span><select aria-label="시각화할 CSV 파일" value={resolvedArtifactId} onChange={(event) => {
          setArtifactId(Number(event.target.value));
          setPlaying(false);
        }}>
          {csvs.map((item) => <option value={item.id} key={item.id}>{item.member || item.name}</option>)}
        </select></label>
        <div className="playbackControls" aria-label="자세 재생 제어">
          <button type="button" className="textButton" disabled={!playbackAvailable} onClick={() => { setPlaying(false); setCursorTime(start); }}>처음</button>
          <button type="button" className="textButton playbackPrimary" disabled={!playbackAvailable || playing} onClick={startPlayback}>재생</button>
          <button type="button" className="textButton" disabled={!playing} onClick={() => setPlaying(false)}>정지</button>
          <label><span>속도</span><select aria-label="재생 속도" value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
            <option value={0.5}>0.5x</option><option value={1}>1x</option><option value={2}>2x</option><option value={4}>4x</option>
          </select></label>
        </div>
        <div className="playbackTime"><span>시간</span><strong>{cursorLabel}</strong></div>
      </div>
    </header>

    {sortedJoints.length > 0 ? <section className="jointSelector visualizationJointSelector" aria-labelledby="visualization-joint-selector">
      <div className="jointSelectorHead">
        <div><h3 id="visualization-joint-selector">조인트 선택</h3><span>{selectedJoints.length} / {sortedJoints.length}개 선택</span></div>
        <div>
          <button type="button" className="textButton" onClick={() => setSelection(sortedJoints)}>전체 선택</button>
          <button type="button" className="textButton" onClick={() => setSelection([])}>선택 해제</button>
        </div>
      </div>
      <div className="jointGroupActions" role="group" aria-label="시각화 조인트 그룹 선택">
        {jointGroups.map((group) => <button
          type="button"
          className={`textButton${isExactJointSelection(selectedJoints, group.joints) ? " active" : ""}`}
          aria-pressed={isExactJointSelection(selectedJoints, group.joints)}
          aria-label={`시각화 ${group.label} 그룹만 선택`}
          onClick={() => setSelection(group.joints)}
          key={group.key}
        >{group.label}</button>)}
      </div>
      <div className="jointGroupList">
        {jointGroups.map((group) => <section className={`jointGroupBlock jointGroup-${group.key}`} aria-label={`${group.label} 시각화 조인트`} key={group.key}>
          <h4>{group.label}<span>{group.joints.length}</span></h4>
          <div className="jointChecklist">
            {group.joints.map((joint) => <label key={joint}>
              <input type="checkbox" checked={selectedJoints.includes(joint)} onChange={(event) => toggleJoint(joint, event.currentTarget.checked)} />
              <span>{joint}</span>
            </label>)}
          </div>
        </section>)}
      </div>
      {!selectedJoints.length && <p className="selectionNotice">제로 포지션 자세입니다. 조인트를 선택하면 그래프 연동과 재생이 활성화됩니다.</p>}
      {selectedJoints.length > 12 && <p className="selectionNotice warningNotice">표시 신호가 많아 렌더링이 느릴 수 있습니다. 필요한 조인트만 선택하거나 시간 구간을 확대하십시오.</p>}
    </section> : <div className="visualizationNotice">위치 신호가 있는 조인트를 감지하지 못했습니다.</div>}

    {chartLoading && <div className="visualizationNotice">CSV 자세 신호를 불러오는 중입니다.</div>}
    {chartError && <div className="visualizationNotice errorText">{chartError}</div>}
    {payload && <div className="visualizationGrid">
      <div className="visualizationPlotStack">
        <VisualizationPlot
          title="위치"
          subtitle="현재 관절 위치"
          unit="deg"
          category="position"
          series={selectedPositionSeries}
          start={start}
          end={end}
          cursorTime={cursorTime}
          zoomRange={zoomRange}
          onCursorTimeChange={setCursorTime}
          onZoomRangeChange={setZoomRange}
        />
        <VisualizationPlot
          title={resolvedMetric === "current" ? "전류" : "토크"}
          subtitle={resolvedMetric === "current" ? "모터 측정 전류" : "측정 및 피드포워드 토크"}
          unit={resolvedMetric === "current" ? "A" : "Nm"}
          category={resolvedMetric}
          series={lowerSeries}
          start={start}
          end={end}
          cursorTime={cursorTime}
          zoomRange={zoomRange}
          onCursorTimeChange={setCursorTime}
          onZoomRangeChange={setZoomRange}
          actions={<div className="metricSegment" aria-label="하단 Plot 신호 선택">
            <button type="button" className={resolvedMetric === "current" ? "active" : ""} disabled={!currentAvailable} onClick={() => setMetric("current")}>전류</button>
            <button type="button" className={resolvedMetric === "torque" ? "active" : ""} disabled={!torqueAvailable} onClick={() => setMetric("torque")}>토크</button>
          </div>}
        />
      </div>
      <RobotViewer model={model} jointValues={pose} cursorLabel={cursorLabel} />
    </div>}
  </section>;
}

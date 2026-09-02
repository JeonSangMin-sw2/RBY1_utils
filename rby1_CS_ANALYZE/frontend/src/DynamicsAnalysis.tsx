import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { LineChart } from "echarts/charts";
import {
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TitleComponent,
  TooltipComponent,
} from "echarts/components";
import { init, use as registerECharts } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import {
  ApiClient,
  type DynamicsAnomaly,
  type SinglePoseDynamicsResult,
  type TrajectoryDynamicsPayload,
} from "./api";
import { RobotViewer, type RobotModelDescriptor } from "./RobotViewer";
import { UnifiedTimelineBrushBar, type ZoomRange } from "./CsvAnalysis";

registerECharts([
  LineChart,
  DataZoomComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  MarkLineComponent,
  MarkAreaComponent,
  TooltipComponent,
  CanvasRenderer,
]);

type DynamicsSubTab = "discrepancy" | "state";

export type DynamicsAnalysisProps = {
  api: ApiClient;
  caseId: string;
  activeArtifactId?: number;
  availableCsvs: { id: number; name: string }[];
  detectedModel?: RobotModelDescriptor;
  onSelectCsvArtifact?: (id: number) => void;
};

type TooltipItem = {
  color?: string;
  marker?: string;
  seriesName?: string;
  value?: [number, number] | number | string;
};

type MarkLineEntry = {
  xAxis?: number;
  yAxis?: number;
  name: string;
  symbol?: string[];
  lineStyle?: { color: string; type: string; width: number };
  label?: {
    show?: boolean;
    formatter?: string;
    color?: string;
    backgroundColor?: string;
    padding?: number[];
    borderRadius?: number;
    fontWeight?: string;
    position?: string;
  };
};

export function DynamicsAnalysis({
  api,
  caseId,
  activeArtifactId,
  availableCsvs,
  detectedModel,
  onSelectCsvArtifact,
}: DynamicsAnalysisProps) {
  // Sub-tab state
  const [subTab, setSubTab] = useState<DynamicsSubTab>("discrepancy");

  // CSV Trajectory Dynamics State
  const [csvPayload, setCsvPayload] = useState<TrajectoryDynamicsPayload | null>(null);
  const [csvLoading, setCsvLoading] = useState<boolean>(false);
  const [csvError, setCsvError] = useState<string>("");
  const [selectedJoint, setSelectedJoint] = useState<string>("");
  const [cursorIndex, setCursorIndex] = useState<number>(0);
  const [anomalyFilterOnly, setAnomalyFilterOnly] = useState<boolean>(true);
  const [zoomRange, setZoomRange] = useState<ZoomRange>({ start: 0, end: 100 });

  // Timeline playback state
  const [playing, setPlaying] = useState<boolean>(false);
  const [playSpeed, setPlaySpeed] = useState<number>(1.0);
  const playRef = useRef<{ playing: boolean; speed: number }>({ playing: false, speed: 1.0 });

  // FK Link Selection
  const [userRefLink, setUserRefLink] = useState<string>("");
  const [userTargetLink, setUserTargetLink] = useState<string>("");
  const [angleUnit, setAngleUnit] = useState<"deg" | "rad">("deg");

  // Instantaneous Single Pose FK & Gravity Torque result at cursor
  const [instantPose, setInstantPose] = useState<SinglePoseDynamicsResult | null>(null);

  // Unified Multi-Grid ECharts DOM Container Ref & Chart Instance Ref
  const multiChartRef = useRef<HTMLDivElement>(null);
  const multiChartInstRef = useRef<ReturnType<typeof init> | null>(null);

  // Inferred model key
  const modelKey = useMemo(() => {
    if (detectedModel) {
      return `rby1${detectedModel.model}_${detectedModel.version}`;
    }
    return "rby1a_v1.2";
  }, [detectedModel]);

  // Derived effective links for FK
  const effectiveRefLink = useMemo(() => {
    if (userRefLink && csvPayload?.link_names?.includes(userRefLink)) {
      return userRefLink;
    }
    return csvPayload?.base_link || "base";
  }, [userRefLink, csvPayload]);

  const effectiveTargetLink = useMemo(() => {
    if (userTargetLink && csvPayload?.link_names?.includes(userTargetLink)) {
      return userTargetLink;
    }
    const links = csvPayload?.link_names ?? [];
    const cand = [
      "ee_finger_r1",
      "ee_finger_r2",
      "Link_6R",
      "FT_sensor_R",
      "ee_right",
      links[links.length - 1] ?? "",
    ];
    return cand.find((l) => links.includes(l)) ?? links[links.length - 1] ?? "";
  }, [userTargetLink, csvPayload]);

  // 1. Fetch Trajectory Dynamics for the CSV
  useEffect(() => {
    if (!caseId || !activeArtifactId) {
      return;
    }
    let cancel = false;

    api
      .getTrajectoryDynamics(caseId, activeArtifactId, {
        model: modelKey,
        max_samples: 2000,
      })
      .then((res) => {
        if (!cancel) {
          setCsvPayload(res);
          setCsvError("");
          setCsvLoading(false);
          if (res.joint_names.length > 0) {
            setSelectedJoint((prev) => {
              if (prev && res.joint_names.includes(prev)) return prev;
              const withAnom = res.anomalies[0]?.joint;
              if (withAnom && res.joint_names.includes(withAnom)) return withAnom;
              return res.joint_names.find((j) => j.includes("arm") || j.includes("torso")) ?? res.joint_names[0];
            });
          }
          setCursorIndex(0);
        }
      })
      .catch((err: unknown) => {
        if (!cancel) {
          setCsvError(err instanceof Error ? err.message : String(err));
          setCsvLoading(false);
        }
      });

    return () => {
      cancel = true;
    };
  }, [api, caseId, activeArtifactId, modelKey]);

  // 2. Fetch Instant Forward Kinematics & Full Gravity Torques at Cursor
  useEffect(() => {
    if (!csvPayload || csvPayload.times.length === 0) return;
    const idx = Math.max(0, Math.min(cursorIndex, csvPayload.times.length - 1));

    const currentAngles: Record<string, number> = {};
    for (const j of csvPayload.joint_names) {
      const data = csvPayload.joints[j];
      if (data && data.pos_deg && data.pos_deg[idx] !== undefined) {
        currentAngles[j] = data.pos_deg[idx];
      }
    }

    let cancel = false;
    api
      .calculatePose({
        model: csvPayload.model_key || modelKey,
        joint_angles: currentAngles,
        ref_link: effectiveRefLink,
        target_link: effectiveTargetLink,
        is_deg: true,
      })
      .then((res) => {
        if (!cancel) setInstantPose(res);
      })
      .catch(() => {
        // Silently ignore instant pose fetch error
      });

    return () => {
      cancel = true;
    };
  }, [api, csvPayload, cursorIndex, modelKey, effectiveRefLink, effectiveTargetLink]);

  // 3. Timeline Playback Animation Loop
  useEffect(() => {
    playRef.current = { playing, speed: playSpeed };
  }, [playing, playSpeed]);

  useEffect(() => {
    if (!playing || !csvPayload || csvPayload.times.length <= 1) return;
    let animFrame: number;
    let lastTime = performance.now();
    let virtualTime = csvPayload.times[cursorIndex] ?? csvPayload.times[0];

    const tick = (now: number) => {
      const dt = (now - lastTime) / 1000;
      lastTime = now;
      if (playRef.current.playing) {
        virtualTime += dt * playRef.current.speed;
        const times = csvPayload.times;
        const maxTime = times[times.length - 1];
        if (virtualTime >= maxTime) {
          setCursorIndex(times.length - 1);
          setPlaying(false);
          return;
        }

        // Binary search to find closest sample index matching virtualTime
        let low = 0;
        let high = times.length - 1;
        while (low < high) {
          const mid = (low + high) >> 1;
          if (times[mid] < virtualTime) {
            low = mid + 1;
          } else {
            high = mid;
          }
        }
        setCursorIndex(low);
        animFrame = requestAnimationFrame(tick);
      }
    };

    animFrame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(animFrame);
  }, [playing, csvPayload]);

  // Model descriptor for RobotViewer
  const currentViewerModel = useMemo<RobotModelDescriptor>(() => {
    let modelType: "a" | "m" = "a";
    let versionStr: "v1.0" | "v1.1" | "v1.2" | "v1.3" = "v1.2";
    const key = csvPayload?.model_key || modelKey;
    if (key.includes("rby1m")) modelType = "m";
    if (key.includes("v1.0")) versionStr = "v1.0";
    else if (key.includes("v1.1")) versionStr = "v1.1";
    else if (key.includes("v1.2")) versionStr = "v1.2";
    else if (key.includes("v1.3")) versionStr = "v1.3";

    return {
      model: modelType,
      version: versionStr,
      confidence: detectedModel?.confidence ?? "detected",
      reason: `로봇 모델: ${csvPayload?.model_label ?? key}`,
    };
  }, [csvPayload, modelKey, detectedModel]);

  // Build joint values in radians for 3D viewer at cursor
  const viewerJointValues = useMemo(() => {
    const radMap: Record<string, number> = {};
    if (csvPayload && csvPayload.times.length > 0) {
      const idx = Math.max(0, Math.min(cursorIndex, csvPayload.times.length - 1));
      for (const jName of csvPayload.joint_names) {
        const jData = csvPayload.joints[jName];
        if (jData && jData.pos_deg && jData.pos_deg[idx] !== undefined) {
          radMap[jName] = (jData.pos_deg[idx] * Math.PI) / 180;
        }
      }
    }
    return radMap;
  }, [csvPayload, cursorIndex]);

  // Set of joints with detected anomalies
  const jointsWithAnomalies = useMemo(() => {
    if (!csvPayload) return new Set<string>();
    return new Set(csvPayload.anomalies.map((a: DynamicsAnomaly) => a.joint));
  }, [csvPayload]);

  // Grouped anomalies by joint
  const anomaliesByJoint = useMemo(() => {
    if (!csvPayload) return {};
    const map: Record<string, DynamicsAnomaly[]> = {};
    for (const anom of csvPayload.anomalies) {
      if (!map[anom.joint]) map[anom.joint] = [];
      map[anom.joint].push(anom);
    }
    return map;
  }, [csvPayload]);

  // Grouped joint list
  const groupedJoints = useMemo(() => {
    if (!csvPayload) return {};
    return csvPayload.groups ?? {};
  }, [csvPayload]);

  // Map of joint torque data for the load table
  const jointTorquesMap = useMemo(() => {
    if (!instantPose) return {};
    const map: Record<string, (typeof instantPose.dynamics.joint_torques)[0]> = {};
    for (const item of instantPose.dynamics.joint_torques) {
      map[item.joint] = item;
    }
    return map;
  }, [instantPose]);

  const isDispatchingZoomRef = useRef(false);
  const isInteractingRef = useRef(false);

  useEffect(() => {
    const chart = multiChartInstRef.current;
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

  // 4. One-Time Multi-Grid Chart Initialization (Zero-Lag Native Multi-Grid Engine)
  useEffect(() => {
    if (subTab !== "discrepancy") return;
    if (!multiChartRef.current) return;

    const chart = init(multiChartRef.current);
    multiChartInstRef.current = chart;

    const handleChartClick = (params: unknown) => {
      const p = params as { value?: [number, number] | number; dataIndex?: number };
      if (Array.isArray(p?.value) && typeof p.value[0] === "number") {
        const tClicked = p.value[0];
        if (csvPayload?.times && csvPayload.times.length > 0) {
          const tMin = csvPayload.times[0] ?? 0;
          const foundIdx = csvPayload.times.findIndex((t) => t - tMin >= tClicked);
          if (foundIdx !== -1) setCursorIndex(foundIdx);
        }
      } else if (typeof p?.dataIndex === "number") {
        setCursorIndex(p.dataIndex);
      }
    };
    chart.on("click", handleChartClick);

    const handleZoom = (event: unknown) => {
      if (isDispatchingZoomRef.current) return;
      const value = event as { start?: number; end?: number; batch?: { start?: number; end?: number }[] };
      const range = value.batch?.[0] ?? value;
      if (typeof range.start !== "number" || typeof range.end !== "number") return;
      setZoomRange({ start: range.start, end: range.end });
    };
    chart.on("datazoom", handleZoom);

    const handleMouseDown = () => {
      isInteractingRef.current = true;
    };
    const handleMouseUp = () => {
      setTimeout(() => {
        isInteractingRef.current = false;
      }, 50);
    };

    chart.getZr().on("mousedown", handleMouseDown);
    chart.getZr().on("mouseup", handleMouseUp);
    window.addEventListener("mouseup", handleMouseUp);

    const handleResize = () => {
      chart.resize();
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      window.removeEventListener("mouseup", handleMouseUp);
      chart.getZr().off("mousedown", handleMouseDown);
      chart.getZr().off("mouseup", handleMouseUp);
      chart.off("click", handleChartClick);
      chart.off("datazoom", handleZoom);
      chart.dispose();
      multiChartInstRef.current = null;
    };
  }, [subTab, csvPayload]);

  // 5. High-Performance Multi-Grid Option Updating (60FPS Silky Smooth, Locked Sync)
  useEffect(() => {
    const chart = multiChartInstRef.current;
    if (!chart || !csvPayload || !selectedJoint) return;

    const jData = csvPayload.joints[selectedJoint];
    if (!jData) return;

    const times = csvPayload.times;
    const tMin = times[0] ?? 0;
    const relTimes = times.map((t) => Number((t - tMin).toFixed(3)));
    const totalDurationVal = times.length > 1 ? Number((times[times.length - 1] - tMin).toFixed(3)) : 1;
    const currentSec = Number(((times[cursorIndex] ?? times[0] ?? 0) - tMin).toFixed(3));

    // Continuous [t, val] coordinate points
    const toPoints = (vals: number[]) => vals.map((v, i) => [relTimes[i] ?? 0, v]);

    // Torque series
    const actualTq = toPoints(jData.tau_actual);
    const modelTq = toPoints(jData.tau_model);
    const gravTq = toPoints(jData.tau_gravity);
    const ffTq = toPoints(jData.tau_target_ff);
    const extTq = toPoints(jData.tau_ext);
    const tqLim = jData.torque_limit;

    // Position series
    const actualPos = toPoints(jData.pos_deg);
    const targetPos = toPoints(jData.target_pos_deg);
    const posErr = toPoints(jData.pos_error_deg);

    // Velocity series
    const actualVel = toPoints(jData.vel_deg_s);
    const targetVel = toPoints(jData.target_vel_deg_s);
    const velErr = toPoints(jData.vel_error_deg_s);

    // Synchronized vertical cyan cursor markline across ALL 3 subplots
    const cursorMarkLineItem: MarkLineEntry = {
      name: "현재 시점",
      xAxis: currentSec,
      symbol: ["none", "none"],
      lineStyle: { color: "#00ADB5", width: 1.8, type: "dashed" },
      label: { show: false },
    };

    // Horizontal limit lines on torque subplot
    const torqueMarkLines: MarkLineEntry[] = [cursorMarkLineItem];
    if (tqLim) {
      torqueMarkLines.push({
        yAxis: tqLim,
        name: `+한계 (${tqLim.toFixed(1)}Nm)`,
        lineStyle: { color: "rgba(255, 46, 99, 0.75)", type: "dashed", width: 1.2 },
        label: { show: true, formatter: `+한계 (${tqLim.toFixed(0)}Nm)`, color: "#ff6b8b", position: "insideEndTop" },
      });
      torqueMarkLines.push({
        yAxis: -tqLim,
        name: `-한계 (-${tqLim.toFixed(1)}Nm)`,
        lineStyle: { color: "rgba(255, 46, 99, 0.75)", type: "dashed", width: 1.2 },
        label: { show: true, formatter: `-한계 (-${tqLim.toFixed(0)}Nm)`, color: "#ff6b8b", position: "insideEndBottom" },
      });
    }

    const multiOption = {
      backgroundColor: "transparent",
      animation: false,
      title: [
        {
          text: `📊 [1] 관절 토크 분석 (Nm)  ·  ${selectedJoint}`,
          top: 8,
          left: 14,
          textStyle: { color: "#00ADB5", fontSize: 11.5, fontWeight: "bold" },
        },
        {
          text: `📐 [2] 관절 위치 분석 (deg)`,
          top: "36%",
          left: 14,
          textStyle: { color: "#10B981", fontSize: 11.5, fontWeight: "bold" },
        },
        {
          text: `🚀 [3] 관절 속도 분석 (deg/s)`,
          top: "69%",
          left: 14,
          textStyle: { color: "#38BDF8", fontSize: 11.5, fontWeight: "bold" },
        },
      ],
      legend: [
        {
          top: 8,
          right: 18,
          type: "scroll",
          itemGap: 10,
          textStyle: { color: "#CCC", fontSize: 9.5 },
          pageIconColor: "#00ADB5",
          pageIconInactiveColor: "#444",
          data: [
            "측정 τ (Actual)",
            "이론 τ (Model)",
            "중력 τ (Gravity)",
            "목표 FF (Target)",
            "외란/잔차 Δτ (Residual)",
          ],
        },
        {
          top: "36%",
          right: 18,
          type: "scroll",
          itemGap: 10,
          textStyle: { color: "#CCC", fontSize: 9.5 },
          pageIconColor: "#10B981",
          pageIconInactiveColor: "#444",
          data: ["실제 위치 (Actual)", "목표 위치 (Target)", "위치 오차 (Error)"],
        },
        {
          top: "69%",
          right: 18,
          type: "scroll",
          itemGap: 10,
          textStyle: { color: "#CCC", fontSize: 9.5 },
          pageIconColor: "#38BDF8",
          pageIconInactiveColor: "#444",
          data: ["실제 속도 (Actual)", "목표 속도 (Target)", "속도 오차 (Error)"],
        },
      ],
      grid: [
        { id: "gTorque", top: 34, height: "23%", left: 62, right: 18 },
        { id: "gPos", top: "40.5%", height: "22%", left: 62, right: 18 },
        { id: "gVel", top: "73.5%", height: "18%", left: 62, right: 18 },
      ],
      xAxis: [
        {
          gridIndex: 0,
          type: "value",
          min: 0,
          max: totalDurationVal,
          show: false,
          splitLine: { lineStyle: { color: "rgba(57, 62, 70, 0.3)" } },
        },
        {
          gridIndex: 1,
          type: "value",
          min: 0,
          max: totalDurationVal,
          show: false,
          splitLine: { lineStyle: { color: "rgba(57, 62, 70, 0.3)" } },
        },
        {
          gridIndex: 2,
          type: "value",
          min: 0,
          max: totalDurationVal,
          show: true,
          axisLine: { lineStyle: { color: "#393E46" } },
          splitLine: { lineStyle: { color: "rgba(57, 62, 70, 0.3)" } },
          axisLabel: { color: "#888", formatter: "{value} s" },
        },
      ],
      yAxis: [
        {
          gridIndex: 0,
          type: "value",
          splitLine: { lineStyle: { color: "rgba(57, 62, 70, 0.35)" } },
          axisLabel: { color: "#AAA", margin: 6 },
        },
        {
          gridIndex: 1,
          type: "value",
          splitLine: { lineStyle: { color: "rgba(57, 62, 70, 0.3)" } },
          axisLabel: { color: "#AAA", margin: 6 },
        },
        {
          gridIndex: 2,
          type: "value",
          splitLine: { lineStyle: { color: "rgba(57, 62, 70, 0.3)" } },
          axisLabel: { color: "#AAA", margin: 6 },
        },
      ],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "cross", snap: true },
        backgroundColor: "rgba(18, 22, 28, 0.95)",
        borderColor: "#00ADB5",
        padding: [6, 10],
        textStyle: { color: "#EEEEEE", fontSize: 11 },
        formatter: (params: unknown) => {
          if (!Array.isArray(params) || params.length === 0) return "";
          const first = params[0] as TooltipItem;
          const tVal = Array.isArray(first?.value) ? first.value[0] : currentSec;
          let html = `<div style="font-weight:bold;color:#00ADB5;margin-bottom:3px;">⏱ ${Number(tVal).toFixed(3)}s · 동역학 데이터 (${selectedJoint})</div>`;
          (params as TooltipItem[]).forEach((item) => {
            const rawVal = Array.isArray(item.value) ? item.value[1] : item.value;
            const unit = item.seriesName?.includes("토크") || item.seriesName?.includes("τ") ? "Nm" : item.seriesName?.includes("위치") ? "°" : "°/s";
            const val = typeof rawVal === "number" ? `${rawVal.toFixed(2)} ${unit}` : rawVal ?? "";
            html += `<div style="display:flex;justify-content:space-between;gap:12px;margin:2px 0;">
              <span style="color:${item.color ?? "#fff"}">${item.marker ?? ""} ${item.seriesName ?? ""}:</span>
              <strong style="color:#fff">${val}</strong>
            </div>`;
          });
          return html;
        },
      },
      dataZoom: [
        {
          type: "inside",
          xAxisIndex: [0, 1, 2],
          filterMode: "none",
          zoomOnMouseWheel: true,
          moveOnMouseMove: true,
          moveOnMouseWheel: false,
          start: zoomRange.start,
          end: zoomRange.end,
        },
      ],
      series: [
        // --- Grid 0: Torque ---
        {
          name: "측정 τ (Actual)",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: actualTq,
          showSymbol: false,
          itemStyle: { color: "#00FF66" },
          lineStyle: { width: 2, type: "solid" },
          markLine: { data: torqueMarkLines, symbol: ["none", "none"] },
        },
        {
          name: "이론 τ (Model)",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: modelTq,
          showSymbol: false,
          itemStyle: { color: "#00ADB5" },
          lineStyle: { width: 1.8, type: "solid" },
        },
        {
          name: "중력 τ (Gravity)",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: gravTq,
          showSymbol: false,
          itemStyle: { color: "#FF9F43" },
          lineStyle: { width: 1.5, type: "solid" },
        },
        {
          name: "목표 FF (Target)",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: ffTq,
          showSymbol: false,
          itemStyle: { color: "#A855F7" },
          lineStyle: { width: 1.5, type: "solid" },
        },
        {
          name: "외란/잔차 Δτ (Residual)",
          type: "line",
          xAxisIndex: 0,
          yAxisIndex: 0,
          data: extTq,
          showSymbol: false,
          itemStyle: { color: "#FF2E63" },
          lineStyle: { width: 2, type: "dashed" },
        },

        // --- Grid 1: Position ---
        {
          name: "실제 위치 (Actual)",
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: actualPos,
          showSymbol: false,
          itemStyle: { color: "#00FF66" },
          lineStyle: { width: 2, type: "solid" },
          markLine: { data: [cursorMarkLineItem], symbol: ["none", "none"] },
        },
        {
          name: "목표 위치 (Target)",
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: targetPos,
          showSymbol: false,
          itemStyle: { color: "#A855F7" },
          lineStyle: { width: 1.8, type: "solid" },
        },
        {
          name: "위치 오차 (Error)",
          type: "line",
          xAxisIndex: 1,
          yAxisIndex: 1,
          data: posErr,
          showSymbol: false,
          itemStyle: { color: "#FF2E63" },
          lineStyle: { width: 1.8, type: "dashed" },
        },

        // --- Grid 2: Velocity ---
        {
          name: "실제 속도 (Actual)",
          type: "line",
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: actualVel,
          showSymbol: false,
          itemStyle: { color: "#00FF66" },
          lineStyle: { width: 2, type: "solid" },
          markLine: { data: [cursorMarkLineItem], symbol: ["none", "none"] },
        },
        {
          name: "목표 속도 (Target)",
          type: "line",
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: targetVel,
          showSymbol: false,
          itemStyle: { color: "#A855F7" },
          lineStyle: { width: 1.8, type: "solid" },
        },
        {
          name: "속도 오차 (Error)",
          type: "line",
          xAxisIndex: 2,
          yAxisIndex: 2,
          data: velErr,
          showSymbol: false,
          itemStyle: { color: "#FF2E63" },
          lineStyle: { width: 2, type: "dashed" },
        },
      ],
    };

    chart.setOption(multiOption, { notMerge: true, lazyUpdate: false });
  }, [subTab, csvPayload, selectedJoint, cursorIndex]);

  // Diagnostic reason description helper
  const getAnomalyDescription = (anom: DynamicsAnomaly) => {
    if (anom.type.includes("jam") || anom.type.includes("collision")) {
      return "이론치 대비 실측 토크가 비정상적으로 급증했습니다. 외력 충돌, 기계적 걸림(Jam), 하모닉 드라이브 부하를 점검하십시오.";
    }
    if (anom.type.includes("error")) {
      return "목표 궤적과 실제 관절 각도 간 추종 지연이 허용 오차를 초과했습니다. 가감속 프로파일 및 관성 부하를 점검하십시오.";
    }
    if (anom.type.includes("overload")) {
      return "관절 토크가 정격 한계치(95% 이상)에 도달했습니다. 장시간 지속 시 드라이버 발열 및 트립 위험이 있습니다.";
    }
    return anom.summary;
  };

  const totalDuration = csvPayload && csvPayload.times.length > 0
    ? (csvPayload.times[csvPayload.times.length - 1] - csvPayload.times[0]).toFixed(3)
    : "0.000";
  const currentCursorSec = csvPayload && csvPayload.times[cursorIndex] !== undefined
    ? (csvPayload.times[cursorIndex] - (csvPayload.times[0] ?? 0)).toFixed(3)
    : "0.000";

  return (
    <div className="dynamicsContainer">
      {/* 1. Main Header with Subtab Navigation */}
      <header className="dynamicsHeader">
        <div className="dynamicsTitleGroup">
          <span className="dynamicsTitle">⚡ 다이나믹스 분석</span>
          {/* Subtabs Switcher */}
          <div className="dynamicsSubTabs">
            <button
              type="button"
              className={`subTabBtn ${subTab === "discrepancy" ? "active" : ""}`}
              onClick={() => setSubTab("discrepancy")}
            >
              📈 동역학 불일치 분석
            </button>
            <button
              type="button"
              className={`subTabBtn ${subTab === "state" ? "active" : ""}`}
              onClick={() => setSubTab("state")}
            >
              📐 상태 분석 (기구학 & 부하율)
            </button>
          </div>
          <span className="modelTagBadge">
            🤖 모델: {csvPayload?.model_label ?? (detectedModel ? `${detectedModel.model.toUpperCase()} Type · ${detectedModel.version.toUpperCase()}` : "RBY1-A V1.2")} (자동 감지)
          </span>
        </div>

        <div className="dynamicsHeaderControls">
          {availableCsvs.length > 1 && (
            <div className="controlGroup">
              <label htmlFor="csvArtifactSelect">CSV 파일:</label>
              <select
                id="csvArtifactSelect"
                value={activeArtifactId ?? ""}
                onChange={(e) => onSelectCsvArtifact?.(Number(e.target.value))}
              >
                {availableCsvs.map((csv) => (
                  <option key={csv.id} value={csv.id}>
                    {csv.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* FK Reference and Target Link Selectors for State Analysis */}
          {subTab === "state" && csvPayload?.link_names && csvPayload.link_names.length > 0 && (
            <div className="fkLinksGroup">
              <div className="controlGroup">
                <label htmlFor="refLinkSel">Ref Link:</label>
                <select
                  id="refLinkSel"
                  value={effectiveRefLink}
                  onChange={(e) => setUserRefLink(e.target.value)}
                >
                  {csvPayload.link_names.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </div>
              <div className="controlGroup">
                <label htmlFor="targetLinkSel">Target Link:</label>
                <select
                  id="targetLinkSel"
                  value={effectiveTargetLink}
                  onChange={(e) => setUserTargetLink(e.target.value)}
                >
                  {csvPayload.link_names.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}

          <div className="unitToggleGroup">
            <span className="unitLabel">단위:</span>
            <button
              type="button"
              className={`unitBtn ${angleUnit === "deg" ? "active" : ""}`}
              onClick={() => setAngleUnit("deg")}
            >
              Deg (°)
            </button>
            <button
              type="button"
              className={`unitBtn ${angleUnit === "rad" ? "active" : ""}`}
              onClick={() => setAngleUnit("rad")}
            >
              Rad
            </button>
          </div>
        </div>
      </header>

      {/* 2. SUBTAB 1: STATE ANALYSIS (상태 분석: 3D 시뮬 + 타임라인 + 4x4 변환행렬 + 컴포넌트별 고정 부하율 테이블) */}
      {subTab === "state" && (
        <div className="dynamicsStateLayout">
          {/* Left Column: Big 3D Simulation & Top Controller */}
          <div className="stateLeftColumn">
            <div className="state3DViewerCard">
              <div className="sidebarHeader">
                <span>🤖 3D 로봇 자세 시뮬레이션 ({csvPayload?.model_label ?? modelKey})</span>
                <span className="frameIndicator">
                  ⏱ t = {currentCursorSec}s / {totalDuration}s (프레임 {cursorIndex + 1} / {csvPayload?.times.length ?? 0})
                </span>
              </div>

              {/* Simulation Playback Control Bar placed at the TOP of 3D Viewer */}
              <div className="simControlBar">
                <div className="playbackControls" aria-label="자세 재생 제어">
                  <button
                    type="button"
                    className="textButton"
                    disabled={!csvPayload || csvPayload.times.length === 0}
                    onClick={() => {
                      setPlaying(false);
                      setCursorIndex(0);
                    }}
                    title="처음 위치로 이동"
                  >
                    처음
                  </button>
                  <button
                    type="button"
                    className={`textButton playbackPrimary ${playing ? "playing" : ""}`}
                    disabled={!csvPayload || csvPayload.times.length === 0}
                    onClick={() => {
                      if (!playing && cursorIndex >= (csvPayload?.times.length ?? 0) - 1) {
                        setCursorIndex(0);
                      }
                      setPlaying(!playing);
                    }}
                  >
                    {playing ? "일시정지" : "▶ 재생"}
                  </button>
                  <button
                    type="button"
                    className="textButton"
                    disabled={!playing}
                    onClick={() => setPlaying(false)}
                  >
                    정지
                  </button>

                  <button
                    type="button"
                    className="textButton stepBtn"
                    disabled={!csvPayload || cursorIndex <= 0}
                    onClick={() => {
                      setPlaying(false);
                      setCursorIndex((prev) => Math.max(0, prev - 1));
                    }}
                    title="이전 프레임"
                  >
                    ⏮
                  </button>
                  <button
                    type="button"
                    className="textButton stepBtn"
                    disabled={!csvPayload || cursorIndex >= (csvPayload?.times.length ?? 1) - 1}
                    onClick={() => {
                      setPlaying(false);
                      setCursorIndex((prev) => Math.min((csvPayload?.times.length ?? 1) - 1, prev + 1));
                    }}
                    title="다음 프레임"
                  >
                    ⏭
                  </button>

                  <label className="speedSelectLabel">
                    <span>속도</span>
                    <select
                      aria-label="재생 속도"
                      value={playSpeed}
                      onChange={(e) => setPlaySpeed(Number(e.target.value))}
                    >
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
                  <strong>
                    {currentCursorSec}s / {totalDuration}s
                  </strong>
                </div>
              </div>

              {/* Integrated Timeline Scrubber Slider at Top */}
              <div className="simSliderRow">
                <input
                  type="range"
                  className="timelineSlider"
                  min={0}
                  max={Math.max(0, (csvPayload?.times.length ?? 1) - 1)}
                  value={cursorIndex}
                  onChange={(e) => {
                    setPlaying(false);
                    setCursorIndex(Number(e.target.value));
                  }}
                  title="타임라인 슬라이더"
                />
              </div>

              <div className="stateViewerFrame">
                <RobotViewer
                  model={currentViewerModel}
                  jointValues={viewerJointValues}
                  cursorLabel={`t = ${currentCursorSec}s`}
                />
              </div>
            </div>
          </div>

          {/* Right Column: 4x4 Transformation Matrix & Fixed Component-Grouped Load Table */}
          <div className="stateRightColumn">
            {/* 4x4 Homogeneous Transformation Matrix & FK Kinematics Card */}
            {instantPose && (
              <div className="matrixKinematicsCard stateCard">
                <div className="cardHeaderFlex">
                  <h4>📐 순기구학 (FK) & 4x4 동차 변환 행렬 (T)</h4>
                  <span className="linkPathBadge">
                    {effectiveRefLink} ➔ {effectiveTargetLink}
                  </span>
                </div>

                <div className="coordGridCompact">
                  <div className="coordItem">
                    <span>X:</span>
                    <strong>{instantPose.kinematics.position.x_mm.toFixed(2)} mm</strong>
                  </div>
                  <div className="coordItem">
                    <span>Y:</span>
                    <strong>{instantPose.kinematics.position.y_mm.toFixed(2)} mm</strong>
                  </div>
                  <div className="coordItem">
                    <span>Z:</span>
                    <strong>{instantPose.kinematics.position.z_mm.toFixed(2)} mm</strong>
                  </div>

                  <div className="coordItem">
                    <span>Roll:</span>
                    <strong>
                      {angleUnit === "deg"
                        ? `${instantPose.kinematics.rotation.roll_deg.toFixed(2)}°`
                        : `${instantPose.kinematics.rotation.roll_rad.toFixed(4)} rad`}
                    </strong>
                  </div>
                  <div className="coordItem">
                    <span>Pitch:</span>
                    <strong>
                      {angleUnit === "deg"
                        ? `${instantPose.kinematics.rotation.pitch_deg.toFixed(2)}°`
                        : `${instantPose.kinematics.rotation.pitch_rad.toFixed(4)} rad`}
                    </strong>
                  </div>
                  <div className="coordItem">
                    <span>Yaw:</span>
                    <strong>
                      {angleUnit === "deg"
                        ? `${instantPose.kinematics.rotation.yaw_deg.toFixed(2)}°`
                        : `${instantPose.kinematics.rotation.yaw_rad.toFixed(4)} rad`}
                    </strong>
                  </div>
                </div>

                <div className="matrixViewCompact">
                  <span className="matrixTitle">4x4 동차 변환 행렬 T:</span>
                  <pre className="matrixPreCompact">
                    {instantPose.kinematics.matrix
                      .map((row) => `[ ${row.map((v) => v.toFixed(4).padStart(8, " ")).join(" ")} ]`)
                      .join("\n")}
                  </pre>
                </div>

                <div className="comRowCompact">
                  <span>무게중심 (CoM):</span>
                  <strong>
                    X: {instantPose.center_of_mass.x_m.toFixed(3)}m, Y: {instantPose.center_of_mass.y_m.toFixed(3)}m, Z:{" "}
                    {instantPose.center_of_mass.z_m.toFixed(3)}m
                  </strong>
                </div>
              </div>
            )}

            {/* Fixed Component-Grouped Gravity Torques & Limit Load Ratio Analysis Table */}
            {instantPose && (
              <div className="dynamicsTorqueTableCard stateCard flexTableCard">
                <div className="torqueTableHeader">
                  <div className="torqueTableTitle">
                    <span>⚡ 컴포넌트별 관절 중력 보상 토크 & 한계 부하율 분석</span>
                    <span className="timeIndicator">t = {currentCursorSec}s</span>
                  </div>
                  <span
                    className={`maxLoadBadge ${
                      instantPose.dynamics.max_gravity_ratio >= 1.0
                        ? "overload"
                        : instantPose.dynamics.max_gravity_ratio >= 0.8
                        ? "warning"
                        : "ok"
                    }`}
                  >
                    최대 부하: {instantPose.dynamics.max_gravity_joint} (
                    {(instantPose.dynamics.max_gravity_ratio * 100).toFixed(1)}%)
                  </span>
                </div>

                <div className="torqueTableScroll">
                  <table className="dynamicsTorqueTable">
                    <thead>
                      <tr>
                        <th>컴포넌트 / 관절명</th>
                        <th>현재 각도</th>
                        <th>중력 토크 (τ_g)</th>
                        <th>정격 한계 (τ_lim)</th>
                        <th>부하율 / 상태</th>
                      </tr>
                    </thead>
                    <tbody>
                      {Object.entries(groupedJoints).map(([grpName, jList]) => (
                        <Fragment key={grpName}>
                          <tr className="componentGroupHeaderRow">
                            <td colSpan={5}>
                              <strong>📂 {grpName} ({jList.length} DOF)</strong>
                            </td>
                          </tr>
                          {jList.map((jName) => {
                            const item = jointTorquesMap[jName];
                            if (!item) return null;
                            return (
                              <tr
                                key={item.joint}
                                className={`status-${item.status.toLowerCase()} ${
                                  item.joint === selectedJoint ? "selectedJointRow" : ""
                                }`}
                                onClick={() => setSelectedJoint(item.joint)}
                              >
                                <td className="jointCell indentedJoint">
                                  <strong>{item.joint}</strong>
                                </td>
                                <td>
                                  {angleUnit === "deg"
                                    ? `${item.position_deg.toFixed(2)}°`
                                    : `${item.position_rad.toFixed(4)} rad`}
                                </td>
                                <td className="tqVal">{item.gravity_torque.toFixed(3)} Nm</td>
                                <td>{item.torque_limit ? `${item.torque_limit.toFixed(1)} Nm` : "Inf / N/A"}</td>
                                <td>
                                  <span className={`statusPill ${item.status.toLowerCase()}`}>
                                    {item.status} ({(item.load_ratio * 100).toFixed(1)}%)
                                  </span>
                                </td>
                              </tr>
                            );
                          })}
                        </Fragment>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* 3. SUBTAB 2: DISCREPANCY ANALYSIS (Single High-Performance Multi-Grid Canvas) */}
      {subTab === "discrepancy" && (
        <div className="dynamicsDiscrepancyContainer">
          {/* Joint Navigation Bar */}
          <div className="csvDynamicsToolbar">
            <div className="jointsNavGroup">
              <span className="navLabel">관절 선택:</span>
              <div className="jointPillsScroll">
                {Object.entries(groupedJoints).length > 0
                  ? Object.entries(groupedJoints).map(([grpName, jList]) => {
                      const visibleJoints = jList.filter((j) => !anomalyFilterOnly || jointsWithAnomalies.has(j));
                      if (visibleJoints.length === 0) return null;
                      return (
                        <div key={grpName} className="jointSubsystemPillGroup">
                          <span className="grpBadge">{grpName}</span>
                          {visibleJoints.map((jName) => {
                            const hasAnomaly = jointsWithAnomalies.has(jName);
                            const isSel = jName === selectedJoint;
                            return (
                              <button
                                key={jName}
                                type="button"
                                className={`jointPillBtn ${isSel ? "active" : ""} ${hasAnomaly ? "hasAnomaly" : ""}`}
                                onClick={() => setSelectedJoint(jName)}
                              >
                                {jName}
                                {hasAnomaly && <span className="anomalyDot" title="동역학 이상 감지됨" />}
                              </button>
                            );
                          })}
                        </div>
                      );
                    })
                  : csvPayload?.joint_names.map((jName) => {
                      const hasAnomaly = jointsWithAnomalies.has(jName);
                      if (anomalyFilterOnly && !hasAnomaly) return null;
                      const isSel = jName === selectedJoint;
                      return (
                        <button
                          key={jName}
                          type="button"
                          className={`jointPillBtn ${isSel ? "active" : ""} ${hasAnomaly ? "hasAnomaly" : ""}`}
                          onClick={() => setSelectedJoint(jName)}
                        >
                          {jName}
                          {hasAnomaly && <span className="anomalyDot" title="동역학 이상 감지됨" />}
                        </button>
                      );
                    })}
              </div>
            </div>

            <div className="filterControls">
              <label className="checkboxLabel">
                <input
                  type="checkbox"
                  checked={anomalyFilterOnly}
                  onChange={(e) => setAnomalyFilterOnly(e.target.checked)}
                />
                ⚠️ 이상 발생 관절만 필터 ({jointsWithAnomalies.size}개)
              </label>
            </div>
          </div>

          {/* Main Body: Multi-Grid Canvas + Right Sidebar */}
          <div className="csvDynamicsBody">
            {/* Center Column: Single High-Performance Synchronized Multi-Grid Canvas + Timeline Player */}
            <div className="dynamicsCenterColumn">
              {csvLoading && <div className="loadingBanner">동역학 시계열 및 불일치 잔차 분석 중...</div>}
              {csvError && <div className="errorBanner">오류: {csvError}</div>}
              {!activeArtifactId && (
                <div className="emptyPrompt">
                  <h3>📂 분석할 Fault CSV 파일이 없습니다</h3>
                  <p>CS 사건 로그나 CSV 파일을 업로드하면 동역학 분석이 자동으로 수행됩니다.</p>
                </div>
              )}

              {/* Unified Timeline Brush Bar Controller */}
              {csvPayload && (
                <UnifiedTimelineBrushBar
                  durationSec={parseFloat(totalDuration) || 5.0}
                  zoomRange={zoomRange}
                  onZoomChange={setZoomRange}
                />
              )}

              {/* Single Multi-Grid Canvas Container (Torque + Position + Velocity) */}
              <div className="unifiedMultiGridCard">
                <div ref={multiChartRef} className="unifiedEChartBox" />
              </div>

              {/* Timeline Scrubber Bar below plots */}
              <div className="timelineScrubberCard compactScrubber">
                <div className="scrubberControls">
                  <button
                    type="button"
                    className="playBtn"
                    onClick={() => {
                      if (!playing && cursorIndex >= (csvPayload?.times.length ?? 0) - 1) {
                        setCursorIndex(0);
                      }
                      setPlaying(!playing);
                    }}
                  >
                    {playing ? "⏸ 일시정지" : "▶ 재생"}
                  </button>
                  <button
                    type="button"
                    className="stepBtn"
                    onClick={() => setCursorIndex((prev) => Math.max(0, prev - 1))}
                  >
                    ⏮
                  </button>
                  <button
                    type="button"
                    className="stepBtn"
                    onClick={() => setCursorIndex((prev) => Math.min((csvPayload?.times.length ?? 1) - 1, prev + 1))}
                  >
                    ⏭
                  </button>

                  <div className="timeReadout">
                    ⏱ <strong>{currentCursorSec}s</strong> / {totalDuration}s
                  </div>

                  <div className="speedButtons">
                    {[0.5, 1.0, 2.0].map((s) => (
                      <button
                        key={s}
                        type="button"
                        className={`speedBtn ${playSpeed === s ? "active" : ""}`}
                        onClick={() => setPlaySpeed(s)}
                      >
                        {s}x
                      </button>
                    ))}
                  </div>
                </div>
              </div>
            </div>

            {/* Middle Column: 1-Column Instantaneous Breakdown Data between Plots and 3D Simulation */}
            {csvPayload && selectedJoint && csvPayload.joints[selectedJoint] && (
              <div className="dynamicsBreakdownColumn">
                <div className="instantBreakdownCard">
                  <div className="instantCardHeader">
                    <div className="instantTitleGroup">
                      <span className="cardIcon">⚡</span>
                      <h4>순간 분해 데이터</h4>
                    </div>
                    <span className="timeIndicator">t = {currentCursorSec}s</span>
                  </div>

                  <div className="instantJointSubheader">
                    <span>선택 관절:</span>
                    <span className="jointHighlightBadge">{selectedJoint}</span>
                  </div>

                  <div className="instantDataSingleColumn">
                    <div className="instantItemCard">
                      <div className="itemMeta">
                        <span className="itemLabel">측정 토크</span>
                        <span className="itemSymbol">τ_act</span>
                      </div>
                      <strong className="tqAct">
                        {csvPayload.joints[selectedJoint].tau_actual[cursorIndex]?.toFixed(2)} <small>Nm</small>
                      </strong>
                    </div>

                    <div className="instantItemCard">
                      <div className="itemMeta">
                        <span className="itemLabel">모델 이론 토크</span>
                        <span className="itemSymbol">τ_model</span>
                      </div>
                      <strong className="tqModel">
                        {csvPayload.joints[selectedJoint].tau_model[cursorIndex]?.toFixed(2)} <small>Nm</small>
                      </strong>
                    </div>

                    <div className="instantItemCard">
                      <div className="itemMeta">
                        <span className="itemLabel">외란/잔차 토크</span>
                        <span className="itemSymbol">Δτ</span>
                      </div>
                      <strong
                        className={`tqExt ${
                          Math.abs(csvPayload.joints[selectedJoint].tau_ext[cursorIndex] ?? 0) > 10
                            ? "anomaly"
                            : ""
                        }`}
                      >
                        {csvPayload.joints[selectedJoint].tau_ext[cursorIndex]?.toFixed(2)} <small>Nm</small>
                      </strong>
                    </div>

                    <div className="instantItemCard">
                      <div className="itemMeta">
                        <span className="itemLabel">중력 보상 토크</span>
                        <span className="itemSymbol">τ_g</span>
                      </div>
                      <strong>
                        {csvPayload.joints[selectedJoint].tau_gravity[cursorIndex]?.toFixed(2)} <small>Nm</small>
                      </strong>
                    </div>

                    <div className="instantItemCard">
                      <div className="itemMeta">
                        <span className="itemLabel">위치 추종 오차</span>
                        <span className="itemSymbol">e_q</span>
                      </div>
                      <strong>
                        {csvPayload.joints[selectedJoint].pos_error_deg[cursorIndex]?.toFixed(2)}<small>°</small>
                      </strong>
                    </div>

                    <div className="instantItemCard">
                      <div className="itemMeta">
                        <span className="itemLabel">속도 추종 오차</span>
                        <span className="itemSymbol">e_v</span>
                      </div>
                      <strong>
                        {csvPayload.joints[selectedJoint].vel_error_deg_s[cursorIndex]?.toFixed(2)}<small>°/s</small>
                      </strong>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Right Column: 3D Robot Simulation + Playback Controls */}
            <div className="dynamicsSimulationColumn">
              <div className="sidebar3DCard">
                <div className="sidebarHeader">
                  <span>🤖 3D 로봇 자세 시뮬레이션 ({csvPayload?.model_label ?? modelKey})</span>
                  <span className="frameIndicator">
                    ⏱ t = {currentCursorSec}s (프레임 {cursorIndex + 1} / {csvPayload?.times.length ?? 0})
                  </span>
                </div>

                {/* Simulation Playback Control Bar placed at the TOP of 3D Viewer */}
                <div className="simControlBar">
                  <div className="playbackControls" aria-label="자세 재생 제어">
                    <button
                      type="button"
                      className="textButton"
                      disabled={!csvPayload || csvPayload.times.length === 0}
                      onClick={() => {
                        setPlaying(false);
                        setCursorIndex(0);
                      }}
                      title="처음 위치로 이동"
                    >
                      처음
                    </button>
                    <button
                      type="button"
                      className={`textButton playbackPrimary ${playing ? "playing" : ""}`}
                      disabled={!csvPayload || csvPayload.times.length === 0}
                      onClick={() => {
                        if (!playing && cursorIndex >= (csvPayload?.times.length ?? 0) - 1) {
                          setCursorIndex(0);
                        }
                        setPlaying(!playing);
                      }}
                    >
                      {playing ? "일시정지" : "▶ 재생"}
                    </button>
                    <button
                      type="button"
                      className="textButton"
                      disabled={!playing}
                      onClick={() => setPlaying(false)}
                    >
                      정지
                    </button>

                    <button
                      type="button"
                      className="textButton stepBtn"
                      disabled={!csvPayload || cursorIndex <= 0}
                      onClick={() => {
                        setPlaying(false);
                        setCursorIndex((prev) => Math.max(0, prev - 1));
                      }}
                      title="이전 프레임"
                    >
                      ⏮
                    </button>
                    <button
                      type="button"
                      className="textButton stepBtn"
                      disabled={!csvPayload || cursorIndex >= (csvPayload?.times.length ?? 1) - 1}
                      onClick={() => {
                        setPlaying(false);
                        setCursorIndex((prev) => Math.min((csvPayload?.times.length ?? 1) - 1, prev + 1));
                      }}
                      title="다음 프레임"
                    >
                      ⏭
                    </button>

                    <label className="speedSelectLabel">
                      <span>속도</span>
                      <select
                        aria-label="재생 속도"
                        value={playSpeed}
                        onChange={(e) => setPlaySpeed(Number(e.target.value))}
                      >
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
                    <strong>
                      {currentCursorSec}s / {totalDuration}s
                    </strong>
                  </div>
                </div>

                {/* Integrated Timeline Scrubber Slider at Top */}
                <div className="simSliderRow">
                  <input
                    type="range"
                    className="timelineSlider"
                    min={0}
                    max={Math.max(0, (csvPayload?.times.length ?? 1) - 1)}
                    value={cursorIndex}
                    onChange={(e) => {
                      setPlaying(false);
                      setCursorIndex(Number(e.target.value));
                    }}
                    title="타임라인 슬라이더"
                  />
                </div>

                <div className="sidebarViewer">
                  <RobotViewer
                    model={currentViewerModel}
                    jointValues={viewerJointValues}
                    cursorLabel={`t = ${currentCursorSec}s`}
                  />
                </div>
              </div>
            </div>
          </div>

          {/* Bottom Panel: Full Width Anomalies List (Sized to fit up to 5 items comfortably) */}
          <div className="anomaliesBottomSection">
            <div className="anomaliesCardHeader">
              <div className="anomaliesTitleGroup">
                <span className="cardIcon">⚠️</span>
                <h4>동역학 불일치 / 이상 감지 목록 (Dynamic Anomalies)</h4>
              </div>
              <span className="totalAnomBadge">{csvPayload?.anomalies.length ?? 0}건 감지</span>
            </div>

            <div className="anomaliesBottomScrollList">
              {(!csvPayload?.anomalies || csvPayload.anomalies.length === 0) && (
                <div className="noAnomalies">감지된 동역학적 불일치 이상이 없습니다 (정상 동작).</div>
              )}

              {Object.entries(anomaliesByJoint).map(([jointName, jointAnomList]) => {
                const isSelectedJoint = jointName === selectedJoint;
                const hasMajor = jointAnomList.some((a) => a.severity === "major");

                return (
                  <div
                    key={jointName}
                    className={`anomalyJointGroupCard ${isSelectedJoint ? "selectedGroup" : ""}`}
                  >
                    <div
                      className="groupHeader"
                      onClick={() => setSelectedJoint(jointName)}
                    >
                      <span className={`groupDot ${hasMajor ? "major" : "minor"}`} />
                      <strong className="groupJointName">{jointName}</strong>
                      <span className="groupCountBadge">{jointAnomList.length}건</span>
                    </div>

                    <div className="groupItemsList">
                      {jointAnomList.map((anom, aIdx) => {
                        const tMin = csvPayload?.times[0] ?? 0;
                        const relStart = (anom.start_time - tMin).toFixed(2);
                        const relEnd = (anom.end_time - tMin).toFixed(2);
                        const desc = getAnomalyDescription(anom);

                        return (
                          <div
                            key={anom.id}
                            className={`anomalyItem ${anom.severity}`}
                            onClick={() => {
                              setSelectedJoint(anom.joint);
                              const idx = csvPayload?.times.findIndex((t) => t >= anom.start_time) ?? -1;
                              if (idx !== -1) setCursorIndex(idx);
                            }}
                          >
                            <div className="anomalyHeader">
                              <span className="anomIndexBadge">#{aIdx + 1}</span>
                              <span className={`severityBadge ${anom.severity}`}>
                                {anom.severity.toUpperCase()}
                              </span>
                              <span className="anomalyTime">
                                {relStart}s ~ {relEnd}s
                              </span>
                            </div>
                            <p className="anomalySummary">{anom.summary}</p>
                            {desc && desc !== anom.summary && (
                              <p className="anomalyDiagnosis">💡 {desc}</p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

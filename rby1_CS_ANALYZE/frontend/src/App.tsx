import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { ApiClient, exchangeBootstrap, ProgressRecord } from "./api";
import { CsvAnalysis } from "./CsvAnalysis";
import { collectDroppedFiles, filterSupportedFiles } from "./dropFiles";
import { confirmedLogMessage } from "./logText";
import "./styles.css";

type CaseItem = {
  case_id: string;
  created_at: string;
  title?: string;
  display_name?: string;
  filename_jsonl?: string;
  model?: string;
  period?: string;
  event_count?: number;
};
type CountItem = { severity?: string; family?: string; title?: string; count: number; occurrences?: number };
type Overview = {
  case_id: string;
  source_count: number;
  raw_event_count: number;
  incident_count: number;
  critical_count: number;
  fault_count: number;
  error_count: number;
  unknown_count: number;
  first_time?: number | null;
  last_time?: number | null;
  first_raw?: string | null;
  last_raw?: string | null;
  warning_count: number;
  csv_linked_count: number;
  csv_coverage: number;
  affected_components: string[];
  affected_joints: string[];
  severity_counts: CountItem[];
  family_counts: CountItem[];
  layer_counts: { layer: string; label: string; count: number }[];
};
type Incident = {
  id: string;
  primary_event_id: string;
  family: string;
  layer: string;
  fault_level?: "major" | "minor" | null;
  title: string;
  severity: string;
  start_time?: number;
  end_time?: number;
  time_basis?: string;
  start_raw?: string;
  end_raw?: string;
  meaning: string;
  summary: string;
  confidence: string;
  confidence_reason: string;
  occurrence_count: number;
  event_count: number;
  affected_components: string[];
  affected_joints: string[];
  affected_power_rails: string[];
  primary_cause?: string;
  primary_check?: string;
  csv_linked: boolean;
};
type Hypothesis = {
  rank: number;
  text: string;
  confidence: string;
  rationale: string;
  source_rule_id: string;
};
type ActionItem = { kind: string; priority: number; text: string; source_rule_id: string };
type Provenance = { original_name: string; member_name?: string };
type Evidence = {
  id: string;
  role: string;
  rank: number;
  relation: string;
  source_name: string;
  member_name?: string;
  line: number;
  byte_offset: number;
  raw_digest: string;
  excerpt: string;
  severity: string;
  category: string;
  component?: string;
  joint?: string;
  command?: string;
  result?: string;
  time_value?: number;
  time_basis?: string;
  time_raw?: string;
  artifact_sha256: string;
  provenance: Provenance[];
};
type CsvLink = {
  artifact_id: number;
  delta_seconds: number;
  confidence: string;
  reason: string;
  original_name: string;
  sha256: string;
  series_names: string[];
};
type IncidentDetail = {
  incident: Incident;
  hypotheses: Hypothesis[];
  checks: ActionItem[];
  remedies: ActionItem[];
  evidence_gaps: ActionItem[];
  evidence: Evidence[];
  timeline: Evidence[];
  timeline_context_seconds: number;
  timeline_truncated: boolean;
  csv_links: CsvLink[];
};
type WarningItem = { id: number; code: string; message: string; member_name?: string };
type JobProgress = { processed: number; total: number; percent: number };
const UPLOAD_PROGRESS_SHARE = 10;
type DisplayTime = { date: string; clock: string };
type AnalysisTab = "incidents" | "csv";

const SEVERITY_LABELS: Record<string, string> = {
  critical: "고심각도 오류",
  error: "오류",
  warning: "주의",
  info: "정보",
};

const CONFIDENCE_LABELS: Record<string, string> = {
  high: "높음",
  medium: "보통",
  low: "낮음",
};

const ROLE_LABELS: Record<string, string> = {
  root: "최초 오류",
  symptom: "증상",
  status: "상태 전환",
  reaction: "후속 반응",
  command: "선행 명령",
  result_success: "명령 성공",
  result_failure: "명령 실패",
  fallout: "제어 종료",
  measurement: "지연 통계",
  warning: "주의",
  context: "관련 로그",
};

const JOB_STATE_LABELS: Record<string, string> = {
  queued: "대기 중",
  running: "분석 중",
  complete: "완료",
  failed: "실패",
  cancelled: "취소됨",
  cancel_requested: "취소 처리 중",
};

const JOB_PHASE_LABELS: Record<string, string> = {
  preparing: "분석 준비",
  parsing_archive: "압축파일 확인",
  extracting: "압축 구성 확인",
  parsing_csv: "Fault CSV 분석",
  parsing_log: "RPC 로그 분석",
  building_incidents: "장애 사건 구성",
  finalizing: "결과 정리",
  complete: "완료",
};

function pad2(value: number): string {
  return String(value).padStart(2, "0");
}

function shortFraction(value?: string): string {
  return (value ?? "").slice(0, 3).replace(/0+$/, "");
}

function rawTime(raw?: string | null): DisplayTime | null {
  if (!raw) return null;
  const wall = raw.match(/^(\d{2})\/(\d{2})\/\d{2}\s+(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/);
  if (wall) {
    const fraction = shortFraction(wall[6]);
    return { date: `${wall[1]}/${wall[2]}`, clock: `${wall[3]}:${wall[4]}:${wall[5]}${fraction ? `.${fraction}` : ""}` };
  }
  const iso = raw.match(/^\d{4}-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/);
  if (iso) {
    const fraction = shortFraction(iso[6]);
    return { date: `${iso[1]}/${iso[2]}`, clock: `${iso[3]}:${iso[4]}:${iso[5]}${fraction ? `.${fraction}` : ""}` };
  }
  const relative = raw.match(/^(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?/);
  if (relative) {
    const fraction = shortFraction(relative[4]);
    return { date: "--/--", clock: `${relative[1]}:${relative[2]}:${relative[3]}${fraction ? `.${fraction}` : ""}` };
  }
  return null;
}

function epochTime(value?: number | null): DisplayTime {
  if (value === undefined || value === null) return { date: "--/--", clock: "시각 미확인" };
  const date = new Date(value * 1000);
  const fraction = shortFraction(String(date.getMilliseconds()).padStart(3, "0"));
  return {
    date: `${pad2(date.getMonth() + 1)}/${pad2(date.getDate())}`,
    clock: `${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}${fraction ? `.${fraction}` : ""}`,
  };
}

function incidentTime(incident: Incident, end = false): DisplayTime {
  return rawTime(end ? incident.end_raw : incident.start_raw)
    ?? epochTime(end ? incident.end_time : incident.start_time);
}

function wholeSecond(clock: string): string {
  return clock.split(".", 1)[0];
}

function evidenceTime(event: Evidence): DisplayTime {
  return rawTime(event.time_raw) ?? epochTime(event.time_value);
}

function rangeText(incident: Incident): string {
  const start = incidentTime(incident);
  const end = incidentTime(incident, true);
  const startClock = wholeSecond(start.clock);
  const endClock = wholeSecond(end.clock);
  if (start.date === end.date && startClock === endClock) return `${start.date} ${startClock}`;
  if (start.date === end.date) return `${start.date} ${startClock} ~ ${endClock}`;
  return `${start.date} ${startClock} ~ ${end.date} ${endClock}`;
}

function overviewRangeText(
  first?: number | null,
  last?: number | null,
  firstRaw?: string | null,
  lastRaw?: string | null,
): string {
  if (first === undefined || first === null || last === undefined || last === null) return "시각 미확인";
  const start = rawTime(firstRaw) ?? epochTime(first);
  const end = rawTime(lastRaw) ?? epochTime(last);
  const startClock = wholeSecond(start.clock);
  const endClock = wholeSecond(end.clock);
  if (start.date === end.date && startClock === endClock) return `${start.date} ${startClock}`;
  if (start.date === end.date) return `${start.date} ${startClock} ~ ${endClock}`;
  return `${start.date} ${startClock} ~ ${end.date} ${endClock}`;
}

function faultLabel(incident: Incident): string | null {
  if (incident.fault_level === "major" || incident.family === "major_fault") return "Major Fault";
  if (incident.fault_level === "minor" || incident.family === "minor_fault") return "Minor Fault";
  return null;
}

function incidentVisualClass(incident: Incident): string {
  if (incident.fault_level === "major" || incident.family === "major_fault") return "fault-major";
  if (incident.fault_level === "minor" || incident.family === "minor_fault") return "fault-minor";
  return `severity-${incident.severity === "critical" ? "error" : incident.severity}`;
}

function incidentBadge(incident: Incident): string {
  return faultLabel(incident) ?? (SEVERITY_LABELS[incident.severity] ?? incident.severity);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  return `${value >= 10 ? value.toFixed(1) : value.toFixed(2)} ${unit}`;
}

function formatDuration(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  return minutes ? `${minutes}분 ${seconds % 60}초` : `${seconds}초`;
}

function formatPercent(percent: number, processed: number): string {
  if (processed > 0 && percent < 0.1) return "<0.1%";
  return `${Number.isInteger(percent) ? percent.toFixed(0) : percent.toFixed(1)}%`;
}

function displayItem(path: string): string {
  return path.split(/[/\\]/).at(-1) ?? path;
}

function assets(incident: Incident): string {
  const values = incident.affected_joints.length ? incident.affected_joints : incident.affected_components;
  if (!values.length) return "영향 대상 미분류";
  return `${values.slice(0, 3).join(", ")}${values.length > 3 ? ` 외 ${values.length - 3}개` : ""}`;
}

function citation(event: Evidence): string {
  const member = event.member_name ? `!/${event.member_name}` : "";
  return `${event.source_name}${member}:${event.line} · sha256:${event.raw_digest}`;
}

export default function App() {
  const [client, setClient] = useState<ApiClient | null>(null);
  const [sessionChecked, setSessionChecked] = useState(false);
  const [cases, setCases] = useState<CaseItem[]>([]);
  const [caseId, setCaseId] = useState("");
  const [selectedArtifactId, setSelectedArtifactId] = useState<number | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [incidents, setIncidents] = useState<Incident[]>([]);
  const [loadedDetail, setLoadedDetail] = useState<{
    caseId: string;
    incidentId: string;
    value: IncidentDetail;
  } | null>(null);
  const [detailFailure, setDetailFailure] = useState<{
    caseId: string;
    incidentId: string;
    message: string;
  } | null>(null);
  const [detailRetry, setDetailRetry] = useState(0);
  const [selectedId, setSelectedId] = useState("");
  const [warnings, setWarnings] = useState<WarningItem[]>([]);
  const [query, setQuery] = useState("");
  const [severity, setSeverity] = useState("all");
  const [family, setFamily] = useState("all");
  const [layer, setLayer] = useState("all");
  const [faultOnly, setFaultOnly] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [importNotice, setImportNotice] = useState("");
  const [activeJob, setActiveJob] = useState("");
  const [jobProgress, setJobProgress] = useState<JobProgress | null>(null);
  const [analysisElapsed, setAnalysisElapsed] = useState(0);
  const [operationStartedAt, setOperationStartedAt] = useState<number | null>(null);
  const [displayLimit, setDisplayLimit] = useState(500);
  const [activeTab, setActiveTab] = useState<AnalysisTab>("incidents");
  const [dragActive, setDragActive] = useState(false);
  const [timelineInfo, setTimelineInfo] = useState<{
    jsonl_path: string;
    log_path: string;
    filename_jsonl: string;
    filename_log: string;
    event_count: number;
    size_bytes_jsonl: number;
  } | null>(null);
  const [showLoadModal, setShowLoadModal] = useState(false);
  const loadCaseRequest = useRef(0);

  // Case rename and management state
  const [editingCaseId, setEditingCaseId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState("");

  const handleStartRename = (e: React.MouseEvent, item: CaseItem) => {
    e.stopPropagation();
    setEditingCaseId(item.case_id);
    setEditingTitle(item.title || item.display_name || "");
  };

  const handleSaveRename = async (e: React.MouseEvent | React.FormEvent, targetCaseId: string) => {
    e.stopPropagation();
    e.preventDefault();
    if (!client || !targetCaseId) return;
    try {
      await client.json(`/api/cases/${targetCaseId}`, {
        method: "PATCH",
        body: JSON.stringify({ title: editingTitle.trim() }),
      });
      setCases((prev) => prev.map((c) => (c.case_id === targetCaseId ? { ...c, title: editingTitle.trim(), display_name: editingTitle.trim() || c.filename_jsonl } : c)));
      setEditingCaseId(null);
    } catch (err) {
      alert("케이스 이름 변경에 실패했습니다: " + (err instanceof Error ? err.message : String(err)));
    }
  };

  const handleDeleteCase = async (e: React.MouseEvent, item: CaseItem) => {
    e.stopPropagation();
    if (!client) return;
    const name = item.display_name || item.case_id;
    if (!window.confirm(`'${name}' 분석 케이스를 완전히 삭제하시겠습니까?\n(로컬 저장소의 파일이 삭제됩니다)`)) {
      return;
    }
    try {
      await client.json(`/api/cases/${item.case_id}`, { method: "DELETE" });
      setCases((prev) => prev.filter((c) => c.case_id !== item.case_id));
      if (caseId === item.case_id) {
        setCaseId("");
        setOverview(null);
        setIncidents([]);
      }
    } catch (err) {
      alert("케이스 삭제에 실패했습니다: " + (err instanceof Error ? err.message : String(err)));
    }
  };

  useEffect(() => {
    if (!client || !caseId) {
      setTimelineInfo(null);
      return;
    }
    client.json<{
      jsonl_path: string;
      log_path: string;
      filename_jsonl: string;
      filename_log: string;
      event_count: number;
      size_bytes_jsonl: number;
    }>(`/api/v3/cases/${caseId}/timeline/info`)
      .then(setTimelineInfo)
      .catch(() => setTimelineInfo(null));
  }, [caseId, client]);



  useEffect(() => {
    exchangeBootstrap()
      .then(async (api) => {
        if (!api) return;
        const result = await api.json<{ cases: CaseItem[] }>("/api/cases");
        setCases(result.cases);
        setClient(api);
      })
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setSessionChecked(true));
  }, []);

  useEffect(() => {
    if (operationStartedAt === null) return;
    const timer = window.setInterval(
      () => setAnalysisElapsed(Math.floor((Date.now() - operationStartedAt) / 1000)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [operationStartedAt]);

  async function loadCase(api: ApiClient, id: string) {
    const requestId = ++loadCaseRequest.current;
    setBusy("분석 결과를 불러오는 중입니다.");
    setError("");
    try {
      const [overviewResult, incidentResult, warningResult] = await Promise.all([
        api.json<Overview>(`/api/v2/cases/${id}/overview`),
        api.json<{ incidents: Incident[] }>(`/api/v2/cases/${id}/incidents`),
        api.json<{ warnings: WarningItem[] }>(`/api/cases/${id}/warnings`),
      ]);
      if (requestId !== loadCaseRequest.current) return;
      setCaseId(id);
      try { localStorage.setItem("rby1_active_case_id", id); } catch {}
      setOverview(overviewResult);
      setIncidents(incidentResult.incidents);
      setWarnings(warningResult.warnings);
      setLoadedDetail(null);
      setDetailFailure(null);
      setQuery("");
      setSeverity("all");
      setFamily("all");
      setLayer("all");
      setFaultOnly(false);
      setDisplayLimit(500);
      setSelectedId(incidentResult.incidents[0]?.id ?? "");
      setActiveTab(incidentResult.incidents.length ? "incidents" : "csv");
    } catch (reason) {
      if (requestId === loadCaseRequest.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      if (requestId === loadCaseRequest.current) setBusy("");
    }
  }

  async function createCase(files: File[]) {
    if (!client || !files.length || busy || activeJob) return;
    loadCaseRequest.current += 1;
    const totalBytes = files.reduce((total, file) => total + file.size, 0);
    setOperationStartedAt(Date.now());
    setAnalysisElapsed(0);
    setBusy(`파일 ${files.length}개 분석을 준비하고 있습니다.`);
    setJobProgress({ processed: 0, total: totalBytes, percent: 0 });
    setError("");
    try {
      const created = await client.json<{ case_id: string }>("/api/cases", { method: "POST" });
      const body = new FormData();
      files.forEach((file) => body.append("files", file));
      setBusy(`파일 ${files.length}개를 업로드하고 있습니다.`);
      const imported = await client.upload<{ job_id: string }>(
        `/api/cases/${created.case_id}/imports`,
        body,
        (loaded, uploadTotal) => {
          const transferred = uploadTotal && uploadTotal > 0
            ? Math.min(totalBytes, loaded / uploadTotal * totalBytes)
            : Math.min(totalBytes, loaded);
          setJobProgress({
            processed: transferred,
            total: totalBytes,
            percent: totalBytes > 0
              ? Math.round(transferred * UPLOAD_PROGRESS_SHARE * 10 / totalBytes) / 10
              : 0,
          });
        },
      );
      setBusy("업로드가 완료되어 로그 분석을 시작합니다.");
      setJobProgress({ processed: 0, total: totalBytes, percent: UPLOAD_PROGRESS_SHARE });
      setActiveJob(imported.job_id);
      let terminal: ProgressRecord | null = null;
      let lastPhase = "preparing";
      let lastItem = "";
      for await (const record of client.progress(imported.job_id)) {
        if (record.phase) lastPhase = record.phase;
        if (record.current_item) lastItem = record.current_item;
        const counters = record.counters ?? {};
        if (record.counters) {
          setJobProgress((current) => {
            const total = counters.total_bytes ?? counters.received_bytes ?? current?.total ?? totalBytes;
            const processed = counters.processed_bytes ?? current?.processed ?? 0;
            const calculated = total > 0 ? processed * 100 / total : 0;
            const reported = counters.progress_percent ?? calculated;
            const analysisPercent = Math.max(0, Math.min(100, Math.max(reported, calculated)));
            return {
              processed,
              total,
              percent: Math.round((UPLOAD_PROGRESS_SHARE
                + analysisPercent * (100 - UPLOAD_PROGRESS_SHARE) / 100) * 10) / 10,
            };
          });
        }
        const phaseText = JOB_PHASE_LABELS[lastPhase] ? ` · ${JOB_PHASE_LABELS[lastPhase]}` : "";
        const itemText = lastItem ? ` · ${displayItem(lastItem)}` : "";
        const incidentText = counters.incidents === undefined ? "" : ` · 사건 ${counters.incidents}건`;
        setBusy(`${JOB_STATE_LABELS[record.state] ?? record.state}${phaseText}${itemText}${incidentText}`);
        if (["complete", "failed", "cancelled"].includes(record.state)) {
          terminal = record;
          break;
        }
      }
      if (!terminal || terminal.state !== "complete") {
        throw new Error(terminal?.state === "cancelled" ? "로그 가져오기가 취소되었습니다." : "로그 가져오기에 실패했습니다.");
      }
      setCases((await client.json<{ cases: CaseItem[] }>("/api/cases")).cases);
      await loadCase(client, created.case_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setActiveJob("");
      setOperationStartedAt(null);
      setAnalysisElapsed(0);
      setBusy("");
      setJobProgress(null);
    }
  }

  function selectFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = filterSupportedFiles(event.currentTarget.files ?? []);
    event.currentTarget.value = "";
    setImportNotice("");
    if (!files.length) {
      setError("선택한 항목에서 분석 가능한 LOG, CSV 또는 압축파일을 찾지 못했습니다.");
      return;
    }
    void createCase(files);
  }

  function handleDragOver(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    if (!importDisabled) setDragActive(true);
  }

  function handleDragLeave(event: DragEvent<HTMLElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
    setDragActive(false);
  }

  async function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragActive(false);
    if (importDisabled) return;
    setImportNotice("");
    try {
      const { files, readErrorCount } = await collectDroppedFiles(event.dataTransfer);
      if (!files.length) {
        setError("선택한 폴더에서 분석 가능한 LOG, CSV 또는 압축파일을 찾지 못했습니다.");
        return;
      }
      if (readErrorCount) {
        setImportNotice(`폴더에서 읽지 못한 항목 ${readErrorCount}건은 분석에서 제외되었습니다. 원본 폴더를 확인하십시오.`);
      }
      await createCase(files);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "폴더의 파일을 읽지 못했습니다.");
    }
  }

  async function cancelActiveJob() {
    if (!client || !activeJob) return;
    await client.json(`/api/jobs/${activeJob}/cancel`, { method: "POST" });
    setBusy("취소 요청을 처리하고 있습니다.");
  }

  const searchableIncidents = useMemo(() => incidents.map((item) => ({
    item,
    searchText: `${item.title} ${item.summary} ${item.primary_cause ?? ""} ${item.affected_components.join(" ")} ${item.affected_joints.join(" ")}`.toLowerCase(),
  })), [incidents]);
  const severities = useMemo(() => [...new Set(incidents.map((item) => item.severity))], [incidents]);
  const families = useMemo(() => {
    const values = new Map<string, string>();
    incidents.forEach((item) => values.set(item.family, item.title));
    return [...values.entries()].sort((a, b) => a[1].localeCompare(b[1], "ko"));
  }, [incidents]);
  const filtered = useMemo(() => searchableIncidents.filter(({ item, searchText }) => (
    (!faultOnly || Boolean(item.fault_level))
      && (severity === "all" || item.severity === severity)
      && (family === "all" || item.family === family)
      && (layer === "all" || item.layer === layer)
      && searchText.includes(query.trim().toLowerCase())
  )).map(({ item }) => item), [family, faultOnly, layer, query, searchableIncidents, severity]);
  const visible = filtered.slice(0, displayLimit);
  const activeSelectedId = filtered.some((item) => item.id === selectedId)
    ? selectedId
    : filtered[0]?.id ?? "";

  useEffect(() => {
    if (!client || !caseId || !activeSelectedId) return;
    let active = true;
    client.json<IncidentDetail>(`/api/v2/cases/${caseId}/incidents/${activeSelectedId}`)
      .then((value) => {
        if (active) {
          setLoadedDetail({ caseId, incidentId: activeSelectedId, value });
          setDetailFailure(null);
        }
      })
      .catch((reason) => {
        if (active) setDetailFailure({
          caseId,
          incidentId: activeSelectedId,
          message: reason instanceof Error ? reason.message : String(reason),
        });
      });
    return () => { active = false; };
  }, [activeSelectedId, caseId, client, detailRetry]);

  const selected = incidents.find((item) => item.id === activeSelectedId);
  const detail = loadedDetail?.caseId === caseId && loadedDetail.incidentId === activeSelectedId
    ? loadedDetail.value
    : null;
  const detailError = detailFailure?.caseId === caseId && detailFailure.incidentId === activeSelectedId
    ? detailFailure.message
    : "";
  const incidentTimeline = detail?.timeline ?? detail?.evidence ?? [];
  const selectedIndex = visible.findIndex((item) => item.id === activeSelectedId);

  function moveSelection(offset: number) {
    if (!visible.length) return;
    const nextIndex = selectedIndex < 0
      ? 0
      : Math.min(visible.length - 1, Math.max(0, selectedIndex + offset));
    setSelectedId(visible[nextIndex].id);
    window.requestAnimationFrame(() => document.getElementById(`incident-${visible[nextIndex].id}`)?.scrollIntoView({ block: "nearest" }));
  }

  function resetIncidentFilters(nextLayer = "all", onlyFaults = false) {
    setQuery("");
    setSeverity("all");
    setFamily("all");
    setLayer(nextLayer);
    setFaultOnly(onlyFaults);
    setSelectedId("");
    setDisplayLimit(500);
  }

  async function copyPrimaryCitation() {
    const primary = detail?.evidence.find((item) => item.id === detail.incident.primary_event_id) ?? detail?.evidence[0];
    if (primary) await navigator.clipboard.writeText(citation(primary));
  }

  const importDisabled = Boolean(busy || activeJob);
  if (!client) return <main className="sessionScreen">
    <h1>RB-Y1 CS 로그 분석기 V4</h1>
    <p role="status">{error || (sessionChecked
      ? "보안 세션 정보가 없습니다. 설치된 분석기 실행 파일로 다시 시작하십시오."
      : "보안 로컬 세션을 연결하고 있습니다.")}</p>
  </main>;

  return <main
    className={`appShell${dragActive ? " isDragging" : ""}`}
    onDragEnter={handleDragOver}
    onDragOver={handleDragOver}
    onDragLeave={handleDragLeave}
    onDrop={(event) => void handleDrop(event)}
  >
    {dragActive && <div className="dropOverlay" aria-hidden="true"><strong>여기에 파일 또는 폴더를 놓아 분석</strong><span>LOG · CSV · ZIP · TAR · TAR.GZ</span></div>}
    <header className="topbar">
      <div className="brandBlock">
        <span className="versionMark">V4</span>
        <div><p>LOCAL INCIDENT CONSOLE</p><h1>RB-Y1 CS 로그 분석기</h1></div>
      </div>
      <div className="topActions">
        <button
          type="button"
          className={`textButton newCaseButton${!caseId ? " active" : ""}`}
          onClick={() => {
            setCaseId("");
            setShowLoadModal(false);
          }}
          title="새 파일/폴더를 분석하기 위해 초기 화면으로 이동합니다"
        >
          ➕ 새 파일 열기
        </button>
        <button
          type="button"
          className="textButton loadDatasetButton"
          onClick={() => setShowLoadModal(true)}
          title="기존에 분석되어 저장된 통합 파일 목록을 엽니다"
        >
          📂 기존 통합파일 불러오기{cases.length > 0 ? ` (${cases.length})` : ""}
        </button>
      </div>
    </header>

    {showLoadModal && (
      <div className="modalOverlay" role="dialog" aria-modal="true" aria-labelledby="load-modal-title" onClick={() => setShowLoadModal(false)}>
        <div className="modalDialog modalLarge" onClick={(event) => event.stopPropagation()}>
          <div className="modalHeader">
            <h3 id="load-modal-title">📂 기존 통합 파일 관리 및 불러오기</h3>
            <button type="button" className="textButton closeButton" onClick={() => setShowLoadModal(false)}>✕</button>
          </div>
          <div className="modalBody">
            {cases.length === 0 ? (
              <p className="muted">저장된 기존 통합 파일이 없습니다. 새 파일 열기로 분석을 시작하십시오.</p>
            ) : (
              <div className="savedCasesList">
                {cases.map((c) => (
                  <div
                    key={c.case_id}
                    className={`savedCaseCard${c.case_id === caseId ? " activeCard" : ""}`}
                  >
                    {editingCaseId === c.case_id ? (
                      <form className="caseRenameForm" onSubmit={(e) => handleSaveRename(e, c.case_id)} onClick={(e) => e.stopPropagation()}>
                        <input
                          type="text"
                          className="caseRenameInput"
                          value={editingTitle}
                          onChange={(e) => setEditingTitle(e.target.value)}
                          placeholder="케이스 이름을 입력하세요"
                          autoFocus
                        />
                        <div className="caseRenameActions">
                          <button type="submit" className="textButton primary smallBtn">저장</button>
                          <button type="button" className="textButton smallBtn" onClick={(e) => { e.stopPropagation(); setEditingCaseId(null); }}>취소</button>
                        </div>
                      </form>
                    ) : (
                      <>
                        <div className="savedCaseCardTop" onClick={() => { setShowLoadModal(false); void loadCase(client!, c.case_id); }}>
                          <strong title={c.display_name || c.case_id}>{c.display_name || c.case_id}</strong>
                          <span>{c.period ? `기간: ${c.period}` : new Date(c.created_at).toLocaleString("ko-KR")} · {c.event_count ? `${c.event_count.toLocaleString()}건` : ""}</span>
                        </div>
                        <div className="savedCaseCardActions">
                          <button
                            type="button"
                            className="textButton caseActionBtn"
                            onClick={() => {
                              setShowLoadModal(false);
                              void loadCase(client!, c.case_id);
                            }}
                            title="이 케이스를 바로 엽니다"
                          >
                            📂 바로보기
                          </button>
                          <button
                            type="button"
                            className="textButton caseActionBtn"
                            onClick={(e) => handleStartRename(e, c)}
                            title="케이스 이름을 수정합니다"
                          >
                            ✏️ 이름 수정
                          </button>
                          <button
                            type="button"
                            className="textButton caseActionBtn danger"
                            onClick={(e) => handleDeleteCase(e, c)}
                            title="이 케이스를 삭제합니다"
                          >
                            🗑️ 삭제
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    )}

    {(busy || error || importNotice) && <div className={error ? "statusBar errorBar" : importNotice && !busy ? "statusBar noticeBar" : "statusBar"} role="status" aria-live="polite">
      <div className="statusContent">
        <div className="statusLine"><span>{error || busy || importNotice}</span>{jobProgress && !error && <strong>{formatPercent(jobProgress.percent, jobProgress.processed)}</strong>}</div>
        {jobProgress && !error && <>
          <div className="progressTrack" role="progressbar" aria-label="로그 분석 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={jobProgress.percent}>
            <span style={{ width: `${jobProgress.percent}%` }} />
          </div>
          <small>{formatBytes(jobProgress.processed)} / {formatBytes(jobProgress.total)} · 경과 {formatDuration(analysisElapsed)}</small>
        </>}
      </div>
      {activeJob && <button className="textButton danger" onClick={() => void cancelActiveJob()}>분석 취소</button>}
    </div>}

    {caseId && <div className="tabsBarContainer">
      <nav className="analysisTabs" aria-label="분석 화면">
        <button className={activeTab === "incidents" ? "active" : ""} onClick={() => { setActiveTab("incidents"); setTimeout(() => window.dispatchEvent(new Event("resize")), 10); }}>사건 분석 (로그)</button>
        <button className={activeTab === "csv" ? "active" : ""} onClick={() => { setActiveTab("csv"); setTimeout(() => window.dispatchEvent(new Event("resize")), 10); }}>Fault CSV 분석 & 3D 시각화</button>
      </nav>
      <div className="timelineExportActions">
        <a
          className="timelineDownloadLink"
          href={`/api/v3/cases/${caseId}/timeline/download?format=jsonl`}
          download={timelineInfo?.filename_jsonl ?? `case_${caseId}_timeline_consolidated.jsonl`}
          title="시간순으로 정렬된 전체 이벤트 통합본 파일 (JSONL) 다운로드"
        >
          📥 통합 JSONL
        </a>
        <a
          className="timelineDownloadLink"
          href={`/api/v3/cases/${caseId}/timeline/download?format=log`}
          download={timelineInfo?.filename_log ?? `case_${caseId}_timeline_consolidated.log`}
          title="시간순으로 정렬된 전체 통합 로그 파일 (LOG) 다운로드"
        >
          📥 통합 LOG
        </a>
      </div>
    </div>}

    {activeTab === "incidents" && <section className="triageBand" aria-label="장애 분석 요약">
      <button
        type="button"
        className={`triageMetric${!faultOnly && layer === "all" ? " active" : ""}`}
        aria-pressed={!faultOnly && layer === "all"}
        onClick={() => resetIncidentFilters()}
      ><span>장애 사건</span><strong>{overview?.incident_count ?? 0}</strong><small>분석된 사건 수</small></button>
      <button
        type="button"
        className={`triageMetric criticalMetric${faultOnly ? " active" : ""}`}
        aria-pressed={faultOnly}
        onClick={() => resetIncidentFilters("all", true)}
      ><span>중대 사건</span><strong>{overview?.fault_count ?? 0}</strong><small>Major / Minor Fault</small></button>
      <div className="rangeMetric"><span>발생 구간</span><strong className="timeMetric">{overviewRangeText(overview?.first_time, overview?.last_time, overview?.first_raw, overview?.last_raw)}</strong></div>
    </section>}

    {!caseId ? <section className={`emptyState${dragActive ? " dragActive" : ""}`}>
      <div className="emptyDropHero">
        <div className="emptyIndex">01</div>
        <div className="emptyHeroText">
          <h2>분석할 파일을 가져오십시오</h2>
          <p>RPC 로그, Fault CSV, 압축 묶음 또는 해당 파일이 담긴 폴더를 이 화면에 끌어 놓으십시오.</p>
        </div>
        <div className="emptyImportActions">
          <label className="importButton large">파일 선택<input aria-label="분석할 로그 파일 선택" type="file" multiple accept=".log,.csv,.zip,.tar,.gz,.tar.gz,.tgz" onChange={selectFiles} /></label>
          <label className="importButton large">폴더 선택<input
            ref={(element) => {
              element?.setAttribute("webkitdirectory", "");
              element?.setAttribute("directory", "");
            }}
            aria-label="분석할 로그 폴더 선택"
            type="file"
            multiple
            onChange={selectFiles}
          /></label>
        </div>
      </div>

      {cases.length > 0 && (
        <div className="savedCasesSection">
          <div className="savedCasesHeader">
            <h3>📂 이전에 분석된 통합본 목록 ({cases.length}개)</h3>
            <span className="savedCasesSubtitle">저장소의 케이스를 바로 열거나 이름 수정 및 삭제할 수 있습니다.</span>
          </div>
          <div className="savedCasesList">
            {cases.map((c) => (
              <div
                key={c.case_id}
                className="savedCaseCard"
              >
                {editingCaseId === c.case_id ? (
                  <form className="caseRenameForm" onSubmit={(e) => handleSaveRename(e, c.case_id)} onClick={(e) => e.stopPropagation()}>
                    <input
                      type="text"
                      className="caseRenameInput"
                      value={editingTitle}
                      onChange={(e) => setEditingTitle(e.target.value)}
                      placeholder="케이스 이름을 입력하세요"
                      autoFocus
                    />
                    <div className="caseRenameActions">
                      <button type="submit" className="textButton primary smallBtn">저장</button>
                      <button type="button" className="textButton smallBtn" onClick={(e) => { e.stopPropagation(); setEditingCaseId(null); }}>취소</button>
                    </div>
                  </form>
                ) : (
                  <>
                    <div className="savedCaseCardTop" onClick={() => void loadCase(client!, c.case_id)}>
                      <strong title={c.display_name || c.case_id}>{c.display_name || c.case_id}</strong>
                      <span>{c.period ? `기간: ${c.period}` : new Date(c.created_at).toLocaleString("ko-KR")} · {c.event_count ? `${c.event_count.toLocaleString()}건` : ""}</span>
                    </div>
                    <div className="savedCaseCardActions">
                      <button
                        type="button"
                        className="textButton caseActionBtn"
                        onClick={() => void loadCase(client!, c.case_id)}
                        title="이 케이스를 바로 엽니다"
                      >
                        📂 바로보기
                      </button>
                      <button
                        type="button"
                        className="textButton caseActionBtn"
                        onClick={(e) => handleStartRename(e, c)}
                        title="케이스 이름을 수정합니다"
                      >
                        ✏️ 이름 수정
                      </button>
                      <button
                        type="button"
                        className="textButton caseActionBtn danger"
                        onClick={(e) => handleDeleteCase(e, c)}
                        title="이 케이스를 삭제합니다"
                      >
                        🗑️ 삭제
                      </button>
                    </div>
                  </>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </section> : <>
      {warnings.length > 0 && <details className="warningDrawer">
        <summary><strong>가져오기 경고 {warnings.length}건</strong><span>손상되거나 제외된 파일 확인</span></summary>
        <div className="warningList">{warnings.map((item) => <div key={item.id}><code>{item.code}</code><span>{item.member_name ?? item.message}</span></div>)}</div>
      </details>}

      <div className={`tabContainer ${activeTab === "incidents" ? "tabActive" : "tabHidden"}`}>
        <section className="layerSummary" aria-label="이슈 발생 계층별 집계">
          <div className="layerSummaryTitle"><strong>이슈 발생 계층</strong><span>선택한 계층의 사건만 표시</span></div>
          {(overview?.layer_counts ?? []).map((item) => <button
            type="button"
            className={`layerMetric layer-${item.layer}${!faultOnly && layer === item.layer ? " active" : ""}`}
            aria-pressed={!faultOnly && layer === item.layer}
            onClick={() => resetIncidentFilters(item.layer)}
            key={item.layer}
          >
            <span>{item.label}</span><strong>{item.count}</strong>
          </button>)}
          {!overview?.layer_counts?.length && <p>분류할 사건이 없습니다.</p>}
        </section>
        <div className="investigationGrid">
          <section className="incidentPanel" aria-label="장애 사건 목록">
            <div className="panelTitleRow">
              <div><h2>장애 사건</h2><p>전체 {incidents.length}건 · 조건 일치 {filtered.length}건{visible.length < filtered.length ? ` · ${visible.length}건 표시` : ""}</p></div>
            </div>
            <div className="filters">
              <input aria-label="장애 사건 검색" placeholder="오류, 축, 구성요소 검색" value={query} onChange={(event) => { setQuery(event.target.value); setSelectedId(""); setDisplayLimit(500); }} />
              <select aria-label="심각도 필터" value={severity} onChange={(event) => { setSeverity(event.target.value); setSelectedId(""); setDisplayLimit(500); }}>
                <option value="all">모든 심각도</option>
                {severities.map((item) => <option value={item} key={item}>{SEVERITY_LABELS[item] ?? item}</option>)}
              </select>
              <select aria-label="오류 유형 필터" value={family} onChange={(event) => { setFamily(event.target.value); setSelectedId(""); setDisplayLimit(500); }}>
                <option value="all">모든 오류 유형</option>
                {families.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </div>
            <div className="incidentList" aria-label="분석된 장애 사건" tabIndex={0} onKeyDown={(event) => {
              if (event.key === "ArrowDown") { event.preventDefault(); moveSelection(1); }
              if (event.key === "ArrowUp") { event.preventDefault(); moveSelection(-1); }
            }}>
              {visible.length === 0 && <div className="listEmpty">조건에 맞는 장애 사건이 없습니다.</div>}
              {visible.map((item, index) => {
                const time = incidentTime(item);
                const visualClass = incidentVisualClass(item);
                return <button
                  id={`incident-${item.id}`}
                  key={item.id}
                  aria-current={item.id === activeSelectedId ? "true" : undefined}
                  className={`incidentRow ${visualClass}${item.id === activeSelectedId ? " selected" : ""}`}
                  onClick={() => setSelectedId(item.id)}
                >
                  <time><span>{time.date}</span><strong>{wholeSecond(time.clock)}</strong></time>
                  <span className="incidentIndex">{String(index + 1).padStart(2, "0")}</span>
                  <div className="incidentMain">
                    <div className="incidentTitle"><span>{incidentBadge(item)}</span><strong>{item.title}</strong></div>
                    <p>{item.primary_cause ?? item.meaning}</p>
                    <small>{assets(item)}</small>
                  </div>
                  <div className="incidentFlags">
                    {item.occurrence_count > 1 && <span>반복 {item.occurrence_count}</span>}
                    {item.csv_linked && <span className="csvFlag">CSV</span>}
                  </div>
                </button>;
              })}
              {visible.length < filtered.length && <button className="loadMoreButton" onClick={() => setDisplayLimit((value) => value + 500)}>
                다음 {Math.min(500, filtered.length - visible.length)}건 더 보기
              </button>}
            </div>
          </section>

          <section className="incidentDetailPanel" aria-label="선택 사건 상세">
            {selected ? <>
              <div className="selectedHeader">
                <div className={`severityPillar ${incidentVisualClass(selected)}`}><span>{incidentBadge(selected)}</span></div>
                <div>
                  <p>{rangeText(selected)} · 핵심 근거 {selected.event_count}건{detail ? ` · 표시 로그 ${incidentTimeline.length}건` : ""}</p>
                  <h2>{selected.title}</h2>
                  <span>{assets(selected)}</span>
                </div>
                <button className="textButton" onClick={() => void copyPrimaryCitation()}>근거 복사</button>
              </div>
              <section className="meaningBlock">
                <h3>무슨 일이 발생했나</h3>
                <p>{selected.meaning}</p>
              </section>
              <section className="evidenceSequence">
                <div className="sectionHead"><h3>발생 순서</h3><span>사건 직전 2분의 명령·처리 결과 포함</span></div>
                {!detail && !detailError && <div className="loadingLine">사건 근거를 불러오는 중입니다.</div>}
                {detailError && <div className="detailError"><p>{detailError}</p><button className="textButton" onClick={() => { setDetailFailure(null); setDetailRetry((value) => value + 1); }}>다시 시도</button></div>}
                {detail?.timeline_truncated && <div className="timelineNotice">관련 로그가 많아 최대 2,000건까지만 표시합니다.</div>}
                {incidentTimeline.map((item) => {
                  const shown = evidenceTime(item);
                  const isPrimary = item.id === detail?.incident.primary_event_id;
                  return <details className={`evidenceRow role-${item.role}${isPrimary ? " primaryEvidence" : ""}`} key={item.id} open={isPrimary}>
                    <summary>
                      <time><span>{shown.date}</span><strong>{shown.clock}</strong></time>
                      <span className="roleLabel">{ROLE_LABELS[item.role] ?? item.role}</span>
                      <div><strong>{item.component ?? "미분류 구성요소"}</strong>{isPrimary && <span className="primaryEvidenceBadge">대표 장애 로그</span>}<p>{confirmedLogMessage(item.excerpt, item)}</p></div>
                    </summary>
                    <div className="rawEvidence">
                      <p>{item.relation}</p>
                      <dl><dt>원본</dt><dd>{item.source_name}{item.member_name ? ` / ${item.member_name}` : ""}</dd><dt>위치</dt><dd>줄 {item.line}, 바이트 {item.byte_offset}</dd><dt>해시</dt><dd>{item.raw_digest}</dd></dl>
                    </div>
                  </details>;
                })}
              </section>
            </> : <div className="panelEmpty">왼쪽에서 장애 사건을 선택하십시오.</div>}
          </section>

          <aside className="responsePanel" aria-label="원인 및 대응 절차">
            <div className="panelTitleRow"><div><h2>판단 및 대응</h2><p>근거와 추정을 분리한 점검 순서</p></div></div>
            {selected && detail ? <div className="responseScroll">
              <section className="confidenceStrip">
                <span>진단 신뢰도</span><strong className={`confidence-${selected.confidence}`}>{CONFIDENCE_LABELS[selected.confidence] ?? selected.confidence}</strong>
                <p>{selected.confidence_reason}</p>
              </section>
              <section className="responseSection causeSection">
                <h3>가능한 원인</h3>
                {detail.hypotheses.length ? <ol>{detail.hypotheses.map((item) => <li key={`${item.rank}-${item.text}`}><span>{item.rank}</span><p>{item.text}</p></li>)}</ol> : <p className="muted">현재 근거로 제시할 원인 후보가 없습니다.</p>}
              </section>
              <section className="responseSection checkSection">
                <h3>확인할 항목</h3>
                {detail.checks.length ? <ol>{detail.checks.map((item) => <li key={`${item.priority}-${item.text}`}><span>{item.priority}</span><p>{item.text}</p></li>)}</ol> : <p className="muted">현재 근거로 제시할 확인 항목이 없습니다.</p>}
              </section>
              <section className="responseSection remedySection">
                <h3>대응 방법</h3>
                {detail.remedies.length ? <ol>{detail.remedies.map((item) => <li key={`${item.priority}-${item.text}`}><span>{item.priority}</span><p>{item.text}</p></li>)}</ol> : <p className="muted">현재 근거로 제시할 대응 방법이 없습니다.</p>}
              </section>
              {detail.evidence_gaps.length > 0 && <section className="responseSection gapSection">
                <h3>추가로 필요한 근거</h3>
                <ul>{detail.evidence_gaps.map((item) => <li key={item.text}>{item.text}</li>)}</ul>
              </section>}
              <section className="csvEvidence">
                <h3>Fault CSV 연결</h3>
                {detail.csv_links.length ? detail.csv_links.map((item) => <div key={item.artifact_id}><strong>{item.original_name}</strong><p>{item.reason}</p></div>) : <p>사건 시각과 일치하는 Fault CSV가 확인되지 않았습니다.</p>}
              </section>
            </div> : <p className="muted responsePlaceholder">사건을 선택하면 원인 후보와 점검 순서가 표시됩니다.</p>}
          </aside>
        </div>
      </div>
      <div className={`tabContainer ${activeTab === "csv" ? "tabActive" : "tabHidden"}`}>
        <CsvAnalysis client={client} caseId={caseId} incidents={incidents} selectedArtifactId={selectedArtifactId} onSelectArtifactId={setSelectedArtifactId} />
      </div>
    </>}
  </main>;
}

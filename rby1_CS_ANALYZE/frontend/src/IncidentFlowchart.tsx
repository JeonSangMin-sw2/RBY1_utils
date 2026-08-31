import React, { useState, useMemo, useRef, useEffect, useCallback } from "react";
import type { Incident } from "./App";

export type CommandInfo = {
  id: string;
  category: string;
  name_ko: string;
  description: string;
  normal_condition: string;
  abnormal_condition: string;
  action_hint: string;
};

export type Provenance = {
  original_name: string;
  member_name?: string;
};

export type FlowchartItem = {
  id: string;
  role: string;
  flow_role?: "upc" | "rpc" | "command" | "root" | "fault" | "reaction" | "csv_dump" | "warning" | "error" | "context";
  is_primary?: boolean;
  incident_id?: string;
  incident_title?: string;
  command_info?: CommandInfo;
  rank?: number;
  relation: string;
  excerpt: string;
  source_name: string;
  member_name?: string;
  line: number;
  byte_offset: number;
  raw_digest: string;
  severity: string;
  category: string;
  component?: string;
  joint?: string;
  command?: string;
  result?: string;
  time_value?: number;
  time_basis?: string;
  time_raw?: string;
  artifact_sha256?: string;
  provenance?: Provenance[];
};

interface IncidentFlowchartProps {
  timeline: FlowchartItem[];
  primaryEventId?: string;
  incidentTitle: string;
  activeIncidentId?: string;
  currentDateLabel?: string;
  focusTarget?: { incidentId: string; timestamp: number } | null;
  dayIncidents?: Incident[];
  onActiveIncidentChange?: (incidentId: string) => void;
  selectedNodeId?: string | null;
  onSelectNode?: (item: CompactedFlowchartItem) => void;
}

const ROLE_BADGE_CONFIG: Record<string, { label: string; icon: string; className: string }> = {
  upc: { label: "UPC 명령 (송신)", icon: "📡", className: "badgeUpc" },
  rpc: { label: "RPC 처리 (응답)", icon: "⚡", className: "badgeRpc" },
  command: { label: "UPC 제어 명령", icon: "📡", className: "badgeUpc" },
  root: { label: "에러 발생", icon: "⚠️", className: "badgeRoot" },
  fault: { label: "Fault 상태 전환", icon: "🛑", className: "badgeFault" },
  reaction: { label: "보호 반응 (Reaction)", icon: "🛡️", className: "badgeReaction" },
  csv_dump: { label: "Fault CSV 저장", icon: "💾", className: "badgeCsv" },
  warning: { label: "경고 / 루프 지연", icon: "⏱️", className: "badgeWarning" },
  error: { label: "명령 거절/실패", icon: "❌", className: "badgeError" },
  context: { label: "일반 상태", icon: "ℹ️", className: "badgeContext" },
};

function formatNodeTime(raw?: string, value?: number): string {
  if (raw) {
    const match = raw.match(/(\d{2}:\d{2}:\d{2}(?:\.\d{1,3})?)/);
    if (match) return match[1];
    return raw;
  }
  if (typeof value === "number") {
    return `${value.toFixed(3)}s`;
  }
  return "-";
}

function formatTimeDelta(diffSeconds: number): string {
  if (diffSeconds < 0.0005) return "";
  if (diffSeconds < 1) return `+${(diffSeconds * 1000).toFixed(0)}ms`;
  if (diffSeconds < 60) return `+${diffSeconds.toFixed(1)}s`;
  if (diffSeconds < 3600) {
    const mins = Math.floor(diffSeconds / 60);
    const secs = Math.round(diffSeconds % 60);
    return `+${mins}분 ${secs > 0 ? secs + "초" : ""}`;
  }
  const hrs = Math.floor(diffSeconds / 3600);
  const mins = Math.floor((diffSeconds % 3600) / 60);
  return `+${hrs}시간 ${mins > 0 ? mins + "분" : ""}`;
}

export type CompactedFlowchartItem = FlowchartItem & {
  compact_rpc?: FlowchartItem;
  debug_excerpt?: string;
  info_excerpt?: string;
  merged_count?: number;
};

function compactTimeline(items: FlowchartItem[]): CompactedFlowchartItem[] {
  const result: CompactedFlowchartItem[] = [];
  let i = 0;

  while (i < items.length) {
    const current = items[i];

    if (
      current.is_primary ||
      current.flow_role === "root" ||
      current.flow_role === "fault" ||
      current.flow_role === "error" ||
      current.severity === "critical" ||
      current.severity === "error"
    ) {
      result.push(current);
      i++;
      continue;
    }

    const next = i + 1 < items.length ? items[i + 1] : null;

    // Check if the IMMEDIATE NEXT item is a direct normal RPC response or normal INFO response
    if (next) {
      const isNextAbnormal =
        next.is_primary ||
        next.flow_role === "root" ||
        next.flow_role === "fault" ||
        next.flow_role === "error" ||
        next.severity === "critical" ||
        next.severity === "error";

      const curTime = current.time_value ?? 0;
      const nextTime = next.time_value ?? 0;
      const timeDiff = Math.abs(nextTime - curTime);

      // Only compact if immediate next is normal and happened within 1.5s
      if (!isNextAbnormal && timeDiff <= 1.5) {
        // Strict Condition:
        // current MUST be a Request/Command (UPC / Command / Debug request)
        // next MUST be a normal Response/Completion (RPC / Info success response)
        const isCurrentRequest =
          (current.flow_role === "upc" || current.flow_role === "command" || current.severity === "debug") &&
          current.flow_role !== "rpc" &&
          current.role !== "result_success" &&
          current.role !== "status";

        const isNextResponse =
          (next.flow_role === "rpc" || next.role === "result_success" || next.role === "status" || next.severity === "info") &&
          next.flow_role !== "upc" &&
          next.flow_role !== "command";

        if (isCurrentRequest && isNextResponse) {
          const bestCommandInfo = current.command_info || next.command_info;
          const bestComponent = current.component || next.component;
          result.push({
            ...current,
            flow_role: "upc",
            command_info: bestCommandInfo,
            component: bestComponent,
            compact_rpc: next,
            debug_excerpt: current.severity === "debug" ? current.excerpt : (next.severity === "debug" ? next.excerpt : undefined),
            info_excerpt: next.severity === "info" ? next.excerpt : (current.severity === "info" ? current.excerpt : undefined),
            merged_count: 2,
          });
          i += 2;
          continue;
        }
      }
    }

    // Otherwise, push current as its own separate block
    result.push(current);
    i++;
  }

  return result;
}

export const IncidentFlowchart: React.FC<IncidentFlowchartProps> = ({
  timeline,
  primaryEventId,
  incidentTitle,
  activeIncidentId,
  currentDateLabel,
  focusTarget,
  dayIncidents = [],
  onActiveIncidentChange,
  selectedNodeId,
  onSelectNode,
}) => {
  const [filterMode, setFilterMode] = useState<"summary" | "all">("summary");
  const [internalSelectedNodeId, setInternalSelectedNodeId] = useState<string | null>(null);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const isProgrammaticScrollRef = useRef(false);
  const scrollEndTimerRef = useRef<number | null>(null);

  const activeNodeId = selectedNodeId ?? internalSelectedNodeId;

  // Drag-to-scroll state
  const isPointerDownRef = useRef(false);
  const dragStartXRef = useRef(0);
  const scrollStartLeftRef = useRef(0);
  const hasDraggedRef = useRef(false);
  const [isDragging, setIsDragging] = useState(false);

  // Filter timeline: summary vs all, and compact normal RPC responses
  const filteredTimeline = useMemo(() => {
    if (!timeline || timeline.length === 0) return [];
    const base = filterMode === "all"
      ? timeline
      : timeline.filter((item) => {
          if (item.is_primary || item.id === primaryEventId) return true;
          if (item.flow_role === "root" || item.flow_role === "fault" || item.flow_role === "csv_dump") return true;
          if (item.flow_role === "upc" || item.flow_role === "rpc" || item.flow_role === "command" || item.flow_role === "reaction") return true;
          if (item.severity === "critical" || item.severity === "error") return true;
          return false;
        });
    return compactTimeline(base);
  }, [timeline, filterMode, primaryEventId]);

  // Sync initial selected node without erasing explicit user clicks
  useEffect(() => {
    if (focusTarget?.incidentId) {
      const incNode = filteredTimeline.find((n) => n.incident_id === focusTarget.incidentId && n.is_primary) ||
                      filteredTimeline.find((n) => n.incident_id === focusTarget.incidentId);
      if (incNode) {
        setInternalSelectedNodeId(incNode.id);
        onSelectNode?.(incNode);
        return;
      }
    }
    if (!activeNodeId || !filteredTimeline.some((n) => n.id === activeNodeId)) {
      if (primaryEventId && filteredTimeline.some((n) => n.id === primaryEventId)) {
        const pNode = filteredTimeline.find((n) => n.id === primaryEventId);
        if (pNode) {
          setInternalSelectedNodeId(pNode.id);
          onSelectNode?.(pNode);
        }
      } else if (filteredTimeline.length > 0) {
        setInternalSelectedNodeId(filteredTimeline[0].id);
        onSelectNode?.(filteredTimeline[0]);
      }
    }
  }, [focusTarget, primaryEventId, filteredTimeline]);

  // ONLY center/scroll when explicit focusTarget changes (i.e. user clicks left list or focus button)
  useEffect(() => {
    if (!focusTarget?.incidentId) return;
    const targetId = focusTarget.incidentId;
    const el = document.getElementById(`flow-primary-${targetId}`) ||
               document.getElementById(`flow-node-inc-${targetId}`);
    if (el && scrollContainerRef.current) {
      isProgrammaticScrollRef.current = true;
      el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      window.setTimeout(() => {
        isProgrammaticScrollRef.current = false;
      }, 500);
    }
  }, [focusTarget]);

  // Synchronize active incident strictly when dragging finishes / mouse is released
  const syncActiveIncident = useCallback(() => {
    if (isProgrammaticScrollRef.current || !scrollContainerRef.current) return;
    const container = scrollContainerRef.current;
    const containerRect = container.getBoundingClientRect();
    const centerX = containerRect.left + containerRect.width / 2;

    const nodeElements = container.querySelectorAll<HTMLElement>("[data-incident-id]");
    let closestIncidentId: string | null = null;
    let minDistance = Infinity;

    nodeElements.forEach((el) => {
      const rect = el.getBoundingClientRect();
      const nodeCenterX = rect.left + rect.width / 2;
      const dist = Math.abs(nodeCenterX - centerX);
      if (dist < minDistance) {
        minDistance = dist;
        closestIncidentId = el.getAttribute("data-incident-id");
      }
    });

    if (closestIncidentId && closestIncidentId !== activeIncidentId) {
      onActiveIncidentChange?.(closestIncidentId);
    }
  }, [activeIncidentId, onActiveIncidentChange]);

  const handlePointerUp = useCallback(() => {
    if (!isPointerDownRef.current) return;
    const wasDragging = hasDraggedRef.current;
    isPointerDownRef.current = false;
    setIsDragging(false);

    if (wasDragging) {
      syncActiveIncident();
    }
  }, [syncActiveIncident]);

  // Global window pointer release listener to guarantee drag never gets stuck
  useEffect(() => {
    const handleGlobalPointerUp = () => {
      if (isPointerDownRef.current) {
        handlePointerUp();
      }
    };
    window.addEventListener("pointerup", handleGlobalPointerUp);
    window.addEventListener("pointercancel", handleGlobalPointerUp);
    return () => {
      window.removeEventListener("pointerup", handleGlobalPointerUp);
      window.removeEventListener("pointercancel", handleGlobalPointerUp);
    };
  }, [handlePointerUp]);

  const handlePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return;
    if (!scrollContainerRef.current) return;
    isPointerDownRef.current = true;
    hasDraggedRef.current = false;
    dragStartXRef.current = e.clientX;
    scrollStartLeftRef.current = scrollContainerRef.current.scrollLeft;
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!isPointerDownRef.current || !scrollContainerRef.current) return;
    if (e.buttons === 0) {
      handlePointerUp();
      return;
    }
    const dx = e.clientX - dragStartXRef.current;
    if (Math.abs(dx) > 5) {
      if (!hasDraggedRef.current) {
        hasDraggedRef.current = true;
        try {
          e.currentTarget.setPointerCapture(e.pointerId);
        } catch {
          // Ignore
        }
      }
      if (!isDragging) setIsDragging(true);
      scrollContainerRef.current.scrollLeft = scrollStartLeftRef.current - dx;
    }
  };

  const handleScroll = () => {
    if (isProgrammaticScrollRef.current || isPointerDownRef.current) return;
    if (scrollEndTimerRef.current) window.clearTimeout(scrollEndTimerRef.current);
    scrollEndTimerRef.current = window.setTimeout(() => {
      syncActiveIncident();
    }, 200);
  };

  const scrollToActiveRootCause = () => {
    if (!activeIncidentId) return;
    const el = document.getElementById(`flow-primary-${activeIncidentId}`) ||
               document.getElementById(`flow-node-inc-${activeIncidentId}`);
    if (el && scrollContainerRef.current) {
      isProgrammaticScrollRef.current = true;
      el.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
      window.setTimeout(() => {
        isProgrammaticScrollRef.current = false;
      }, 500);
    }
  };

  const handleNodeClick = (node: CompactedFlowchartItem) => {
    if (hasDraggedRef.current) return;
    setInternalSelectedNodeId(node.id);
    onSelectNode?.(node);
    if (node.incident_id && node.incident_id !== activeIncidentId) {
      onActiveIncidentChange?.(node.incident_id);
    }
  };

  return (
    <div className="incidentFlowchartContainer">
      {/* Header toolbar */}
      <div className="flowchartToolbar">
        <div className="flowchartToolbarLeft">
          <span className="flowchartTitle">
            🔄 <strong>{currentDateLabel ? `${currentDateLabel} 통합 플로우차트` : "통합 장애 발생 순서 흐름도"}</strong>
          </span>
          {dayIncidents.length > 0 && (
            <span className="dayIncidentTotalBadge">
              총 {dayIncidents.length}건 사건 연결
            </span>
          )}
        </div>

        <div className="flowchartToolbarRight">
          <button
            type="button"
            className="flowchartActionBtn focusRootBtn"
            onClick={scrollToActiveRootCause}
            title="현재 선택된 사건의 에러 발생 위치로 즉시 이동합니다"
          >
            🎯 선택 사건 에러 발생 위치로 이동
          </button>

          <div className="flowchartFilterToggle" role="group" aria-label="플로우차트 표시 모드">
            <button
              type="button"
              className={`filterToggleBtn ${filterMode === "summary" ? "active" : ""}`}
              onClick={() => setFilterMode("summary")}
              title="UPC/RPC 명령, 오류, 상태전환 단계만 요약하여 표시"
            >
              주요 인과 흐름 ({filteredTimeline.length})
            </button>
            <button
              type="button"
              className={`filterToggleBtn ${filterMode === "all" ? "active" : ""}`}
              onClick={() => setFilterMode("all")}
              title="해당 날짜 전체 시계열 로그 표시"
            >
              전체 로그 ({timeline.length})
            </button>
          </div>
        </div>
      </div>

      {/* Horizontal Flow Track with Drag-to-Scroll */}
      <div
        className={`flowchartScrollWrapper ${isDragging ? "isDraggingFlowchart" : ""}`}
        ref={scrollContainerRef}
        onScroll={handleScroll}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
        onPointerCancel={handlePointerUp}
      >
        <div className="flowchartTrack">
          {filteredTimeline.length === 0 ? (
            <div className="flowchartEmptyState">
              <span>표시할 타임라인 로그가 없습니다.</span>
            </div>
          ) : (
            filteredTimeline.map((node, index) => {
              const isSelected = node.id === activeNodeId;
              const isPrimary = node.is_primary || node.id === primaryEventId;
              const badgeMeta = ROLE_BADGE_CONFIG[node.flow_role || "context"] ?? {
                label: "일반",
                icon: "ℹ️",
                className: "badgeContext",
              };
              const nodeIncId = node.incident_id || "none";
              const isActiveIncident = activeIncidentId ? nodeIncId === activeIncidentId : true;

              // Calculate time difference with previous node
              const prevNode = index > 0 ? filteredTimeline[index - 1] : null;
              const curTimeVal = node.time_value ?? 0;
              const prevTimeVal = prevNode?.time_value ?? 0;
              const timeDiff = prevNode ? Math.max(0, curTimeVal - prevTimeVal) : 0;
              const timeDeltaText = formatTimeDelta(timeDiff);
              const isLongGap = timeDiff > 300;

              const title =
                node.command_info?.name_ko ||
                node.component ||
                node.category ||
                incidentTitle;

              const subtitle = [
                node.component ? `[${node.component}]` : null,
                node.joint ? `[${node.joint}]` : null,
              ]
                .filter(Boolean)
                .join(" ");

              return (
                <React.Fragment key={node.id}>
                  {index > 0 && (
                    <div className={`flowConnector ${isLongGap ? "longGapConnector" : ""}`}>
                      <div className="connectorLine" />
                      {timeDeltaText && (
                        <span className={`connectorDelta ${isLongGap ? "longGapDelta" : ""}`} title="이전 노드와의 경과 시간">
                          {isLongGap ? `⏱️ ${timeDeltaText} 경과` : timeDeltaText}
                        </span>
                      )}
                      <span className="connectorArrow">➔</span>
                    </div>
                  )}

                  <div
                    id={isPrimary ? `flow-primary-${nodeIncId}` : `flow-node-inc-${nodeIncId}`}
                    data-incident-id={nodeIncId}
                    className={`flowNode ${badgeMeta.className}${isSelected ? " isSelected" : ""}${isPrimary ? " isPrimaryNode" : ""}${isActiveIncident ? " activeIncidentNode" : " dimInactiveNode"}`}
                    onClick={() => handleNodeClick(node)}
                    role="button"
                    tabIndex={0}
                  >
                    <div className="flowNodeHeader">
                      <span className="flowNodeStep">STEP {index + 1}</span>
                      <span className="flowNodeTime">{formatNodeTime(node.time_raw, node.time_value)}</span>
                    </div>

                    <div className="flowNodeBadge">
                      <span>{badgeMeta.icon}</span>
                      <strong>{badgeMeta.label}</strong>
                    </div>

                    <div className="flowNodeBody">
                      <strong className="flowNodeTitle" title={title}>
                        {title}
                      </strong>
                      {subtitle && (
                        <span className="flowNodeSubtitle" title={subtitle}>
                          {subtitle}
                        </span>
                      )}
                    </div>

                    {node.compact_rpc && (
                      <div
                        className="flowNodeSuccessStrip"
                        title={`RPC 정상 응답: ${node.compact_rpc.excerpt || "성공"}`}
                      >
                        <span className="successStripIcon">✅</span>
                        <span className="successStripLabel">RPC 처리 완료</span>
                        {node.compact_rpc.time_raw && (
                          <span className="successStripTime">
                            {formatNodeTime(node.compact_rpc.time_raw, node.compact_rpc.time_value)}
                          </span>
                        )}
                      </div>
                    )}

                    {isPrimary && (
                      <div className="primaryGlowMarker" title="사건의 최초 에러 발생 로그">
                        ⚠️ 에러 발생
                      </div>
                    )}
                  </div>
                </React.Fragment>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
};
export default IncidentFlowchart;

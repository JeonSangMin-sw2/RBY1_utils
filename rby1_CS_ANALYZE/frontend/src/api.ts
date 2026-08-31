export type ProgressRecord = {
  seq: number;
  state: string;
  phase?: string;
  current_item?: string;
  counters?: Record<string, number>;
  warning_delta?: string[];
};

const SESSION_KEY = "rby1-analyzer-v4-session";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function clearStoredSession(): void {
  sessionStorage.removeItem(SESSION_KEY);
}

async function responseError(response: Response, fallback: string): Promise<ApiError> {
  let detail: unknown;
  try {
    detail = await response.clone().json();
  } catch {
    detail = undefined;
  }
  if (response.status === 401) clearStoredSession();
  return new ApiError(
    response.status === 401
      ? "보안 세션이 만료되었습니다. 분석기를 다시 실행하십시오."
      : `${fallback}: ${response.status}`,
    response.status,
    detail,
  );
}

export class ApiClient {
  constructor(private readonly token: string) {}
  private headers(extra: HeadersInit = {}): HeadersInit { return { ...extra, Authorization: `Bearer ${this.token}` }; }
  async json<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(path, { ...init, headers: this.headers(init.headers) });
    if (!response.ok) throw await responseError(response, "요청에 실패했습니다");
    return response.json() as Promise<T>;
  }
  async upload<T>(
    path: string,
    body: FormData,
    onProgress: (loaded: number, total: number | null) => void,
  ): Promise<T> {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open("POST", path);
      request.setRequestHeader("Authorization", `Bearer ${this.token}`);
      request.upload.addEventListener("progress", (event) => {
        onProgress(event.loaded, event.lengthComputable ? event.total : null);
      });
      request.addEventListener("error", () => {
        reject(new ApiError("파일 업로드에 실패했습니다.", 0, undefined));
      });
      request.addEventListener("load", () => {
        let detail: unknown;
        try {
          detail = request.responseText ? JSON.parse(request.responseText) : undefined;
        } catch {
          detail = request.responseText;
        }
        if (request.status >= 200 && request.status < 300) {
          resolve(detail as T);
          return;
        }
        if (request.status === 401) clearStoredSession();
        reject(new ApiError(
          request.status === 401
            ? "보안 세션이 만료되었습니다. 분석기를 다시 실행하십시오."
            : `파일 업로드에 실패했습니다: ${request.status}`,
          request.status,
          detail,
        ));
      });
      request.send(body);
    });
  }
  async *progress(jobId: string, afterSeq = 0, signal?: AbortSignal): AsyncGenerator<ProgressRecord> {
    const response = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/stream?after_seq=${afterSeq}`, { headers: this.headers(), signal });
    if (!response.ok) throw await responseError(response, "진행 상태 연결에 실패했습니다");
    if (!response.body) throw new Error("진행 상태 응답 본문이 없습니다.");
    const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read(); buffer += value ?? "";
      const lines = buffer.split("\n"); buffer = lines.pop() ?? "";
      for (const line of lines) if (line.trim()) yield JSON.parse(line) as ProgressRecord;
      if (done) break;
    }
    if (buffer.trim()) yield JSON.parse(buffer) as ProgressRecord;
  }

  async getDynamicsModels(): Promise<{ models: DynamicsModelCatalogItem[] }> {
    return this.json<{ models: DynamicsModelCatalogItem[] }>("/api/v3/dynamics/models");
  }

  async calculatePose(payload: {
    model: string;
    joint_angles: Record<string, number>;
    ref_link?: string;
    target_link?: string;
    is_deg?: boolean;
  }): Promise<SinglePoseDynamicsResult> {
    return this.json<SinglePoseDynamicsResult>("/api/v3/dynamics/calculate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  async getTrajectoryDynamics(
    caseId: string,
    artifactId: number,
    params?: { model?: string; start?: number; end?: number; max_samples?: number },
  ): Promise<TrajectoryDynamicsPayload> {
    const query = new URLSearchParams();
    if (params?.model) query.set("model", params.model);
    if (params?.start !== undefined) query.set("start", String(params.start));
    if (params?.end !== undefined) query.set("end", String(params.end));
    if (params?.max_samples !== undefined) query.set("max_samples", String(params.max_samples));
    const qs = query.toString() ? `?${query.toString()}` : "";
    return this.json<TrajectoryDynamicsPayload>(
      `/api/v3/cases/${encodeURIComponent(caseId)}/csvs/${artifactId}/dynamics${qs}`,
    );
  }
}

export type DynamicsModelLimits = {
  q_lower: number[];
  q_upper: number[];
  qdot_lower: number[];
  qdot_upper: number[];
  torque: number[];
};

export type DynamicsModelCatalogItem = {
  key: string;
  label: string;
  dof: number;
  base_link: string;
  joint_names: string[];
  link_names: string[];
  groups: Record<string, string[]>;
  limits: DynamicsModelLimits;
};

export type SinglePoseKinematics = {
  position: { x_m: number; y_m: number; z_m: number; x_mm: number; y_mm: number; z_mm: number };
  rotation: { roll_deg: number; pitch_deg: number; yaw_deg: number; roll_rad: number; pitch_rad: number; yaw_rad: number };
  matrix: number[][];
};

export type JointTorqueDetail = {
  joint: string;
  position_rad: number;
  position_deg: number;
  gravity_torque: number;
  torque_limit: number | null;
  load_ratio: number;
  status: "OK" | "WARNING" | "OVERLOAD";
};

export type SinglePoseDynamicsResult = {
  model_key: string;
  model_label: string;
  ref_link: string;
  target_link: string;
  kinematics: SinglePoseKinematics;
  center_of_mass: { x_m: number; y_m: number; z_m: number };
  dynamics: {
    joint_torques: JointTorqueDetail[];
    max_gravity_ratio: number;
    max_gravity_joint: string;
  };
};

export type TrajectoryJointData = {
  pos_deg: number[];
  target_pos_deg: number[];
  vel_deg_s: number[];
  target_vel_deg_s: number[];
  acc_deg_s2: number[];
  tau_actual: number[];
  tau_model: number[];
  tau_gravity: number[];
  tau_target_ff: number[];
  tau_ext: number[];
  pos_error_deg: number[];
  vel_error_deg_s: number[];
  torque_limit: number | null;
};

export type DynamicsAnomaly = {
  id: string;
  joint: string;
  type: string;
  severity: "minor" | "major";
  start_time: number;
  end_time: number;
  peak_value: number;
  unit: string;
  summary: string;
};

export type TrajectoryDynamicsPayload = {
  model_key: string;
  model_label: string;
  times: number[];
  joints: Record<string, TrajectoryJointData>;
  joint_names: string[];
  link_names?: string[];
  base_link?: string;
  groups: Record<string, string[]>;
  anomalies: DynamicsAnomaly[];
};

export async function exchangeBootstrap(): Promise<ApiClient | null> {
  const token = new URLSearchParams(location.hash.slice(1)).get("bootstrap");
  if (!token) {
    const stored = sessionStorage.getItem(SESSION_KEY);
    return stored ? new ApiClient(stored) : null;
  }
  history.replaceState(null, "", `${location.pathname}${location.search}`);
  const response = await fetch("/api/session", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bootstrap_token: token }) });
  if (!response.ok) throw await responseError(response, "로컬 보안 세션 연결에 실패했습니다");
  const body = await response.json() as { session_token: string };
  sessionStorage.setItem(SESSION_KEY, body.session_token);
  return new ApiClient(body.session_token);
}

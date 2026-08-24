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
}

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

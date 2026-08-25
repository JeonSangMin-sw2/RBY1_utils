import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, error: null, errorInfo: null };
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an unhandled error:", error, errorInfo);
    this.setState({ error, errorInfo });
  }

  handleReload = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
    window.location.reload();
  };

  render() {
    if (this.state.hasError) {
      return (
        <div style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "100vh",
          backgroundColor: "#111418",
          color: "#e1e7ec",
          fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
          padding: "24px",
          textAlign: "center",
        }}>
          <div style={{
            background: "#1c2127",
            border: "1px solid #3c444d",
            borderRadius: "8px",
            padding: "32px",
            maxWidth: "600px",
            width: "100%",
            boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
          }}>
            <h2 style={{ margin: "0 0 12px", color: "#f7768e", fontSize: "20px" }}>
              ⚠️ 화면 표시 중 오류가 발생했습니다
            </h2>
            <p style={{ color: "#aab4bc", fontSize: "14px", lineHeight: "1.5", margin: "0 0 20px" }}>
              일시적인 렌더링 오류가 발생했습니다. 아래 버튼을 눌러 새로고침하거나 다시 시도해 주십시오.
            </p>
            {this.state.error && (
              <pre style={{
                background: "#0d1013",
                border: "1px solid #272d34",
                borderRadius: "4px",
                padding: "12px",
                color: "#ff9e64",
                fontSize: "11px",
                textAlign: "left",
                overflowX: "auto",
                maxHeight: "220px",
                margin: "0 0 20px",
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}>
                {this.state.error.stack || this.state.error.toString()}
                {this.state.errorInfo?.componentStack ? `\nComponent Stack:${this.state.errorInfo.componentStack}` : ""}
              </pre>
            )}
            <div style={{ display: "flex", gap: "12px", justifyContent: "center" }}>
              <button
                onClick={this.handleReload}
                style={{
                  background: "#65c8b3",
                  color: "#0f1a18",
                  border: "none",
                  borderRadius: "4px",
                  padding: "8px 18px",
                  fontSize: "13px",
                  fontWeight: "bold",
                  cursor: "pointer",
                }}
              >
                화면 새로고침
              </button>
              <button
                onClick={() => this.setState({ hasError: false, error: null, errorInfo: null })}
                style={{
                  background: "#283038",
                  color: "#d0d7de",
                  border: "1px solid #4a545e",
                  borderRadius: "4px",
                  padding: "8px 18px",
                  fontSize: "13px",
                  cursor: "pointer",
                }}
              >
                다시 시도
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

import React from "react";
import { createPortal } from "react-dom";
import { fetchJson } from "../api.js";
import { confirmAction, getErrorMessage, showError, showSuccess } from "../alerts.js";
import { socket } from "../socket.js";
import { useSession } from "../App.jsx";

// ── Error Boundary ────────────────────────────────────────────
class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error) {
    return { hasError: true, error };
  }

  componentDidCatch(error, errorInfo) {
    console.error("AnalyticsReports Error:", error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <section className="section">
          <div className="pagetitle">
            <h1>Live Analytics</h1>
          </div>
          <div className="alert alert-danger" role="alert">
            <i className="bi bi-exclamation-triangle me-2"></i>
            <strong>Render Error:</strong> {this.state.error?.message || "An error occurred while rendering analytics."}
            <br />
            <small className="text-muted">Check browser console for details. Try refreshing the page.</small>
          </div>
        </section>
      );
    }

    return this.props.children;
  }
}

// ── Helpers ───────────────────────────────────────────────────
function fmt(n) {
  return (n ?? 0).toLocaleString();
}

const APP_HEADER_HEIGHT = 56;
const ANALYTICS_CACHE_KEY = "analytics-basic-cache-v1";
const ANALYTICS_CACHE_TTL_MS = 30 * 1000;
const ANALYTICS_FALLBACK_POLL_MS = 30 * 1000;
const ANALYTICS_CONNECTED_POLL_MS = 30 * 1000;
const ANALYTICS_MIN_REFRESH_INTERVAL_MS = 5 * 1000;

function readAnalyticsCache() {
  if (typeof window === "undefined" || !window.sessionStorage) return null;

  try {
    const raw = window.sessionStorage.getItem(ANALYTICS_CACHE_KEY);
    if (!raw) return null;

    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object") return null;
    if (parsed.data === undefined || typeof parsed.timestamp !== "number") return null;

    return {
      data: parsed.data,
      timestamp: parsed.timestamp,
      serialized: JSON.stringify(parsed.data),
      isFresh: Date.now() - parsed.timestamp < ANALYTICS_CACHE_TTL_MS,
    };
  } catch {
    return null;
  }
}

function writeAnalyticsCache(data, timestamp = Date.now()) {
  if (typeof window === "undefined" || !window.sessionStorage) return;

  try {
    window.sessionStorage.setItem(
      ANALYTICS_CACHE_KEY,
      JSON.stringify({ data, timestamp }),
    );
  } catch {
    // Ignore cache write failures and continue with live data only.
  }
}

// ── Interpretation Box ────────────────────────────────────────
function Interpretation({
  icon = "bi-lightbulb",
  color = "#ffc107",
  children,
}) {
  return (
    <div
      style={{
        display: "flex",
        gap: 10,
        alignItems: "flex-start",
        background: color + "0d",
        border: `1px solid ${color}30`,
        borderRadius: 8,
        padding: "10px 14px",
        marginTop: 14,
        fontSize: 12.5,
        color: "#444",
        lineHeight: 1.7,
      }}
    >
      <i
        className={`bi ${icon}`}
        style={{ color, fontSize: 15, marginTop: 1, flexShrink: 0 }}
      ></i>
      <span>{children}</span>
    </div>
  );
}

// ── Import Modal ──────────────────────────────────────────────
function ImportModal({ onImportSuccess }) {
  const [summary, setSummary] = React.useState(null);
  const [uploading, setUploading] = React.useState(false);
  const [result, setResult] = React.useState(null);
  const [showModal, setShowModal] = React.useState(false);
  const [showHistory, setShowHistory] = React.useState(false);
  const [dragging, setDragging] = React.useState(false);
  const fileInputRef = React.useRef(null);

  React.useEffect(() => {
    fetchJson("/api/import-logs/summary")
      .then(setSummary)
      .catch(() => {});
  }, []);
  React.useEffect(() => {
    const h = (e) => {
      if (e.key === "Escape") setShowModal(false);
    };
    if (showModal) document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [showModal]);

  async function readImportResponse(response) {
    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      return response.json();
    }

    const text = await response.text();
    console.error("Import endpoint returned a non-JSON response:", text);

    throw new Error(
      response.status === 401 || response.status === 403
        ? "Your session may have expired. Please sign in again and try the import once more."
        : "The server returned an unexpected response while importing. Please refresh the page and try again."
    );
  }

  async function uploadFile(file) {
    if (!file.name.endsWith(".csv")) {
      setResult({ success: false, message: "Only CSV files accepted." });
      await showError("Invalid File", "Only CSV files accepted.");
      return;
    }
    setUploading(true);
    setResult(null);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/api/import-logs", { method: "POST", body: fd });
      const d = await readImportResponse(r);

      if (!r.ok) {
        const message =
          d?.message ||
          (r.status === 401 || r.status === 403
            ? "Your session may have expired. Please sign in again and try the import once more."
            : "The import could not be completed right now. Please try again.");
        setResult({ success: false, message });
        await showError("Import Failed", message);
        return;
      }

      setResult(d);
      if (d.success) {
        const s = await fetchJson("/api/import-logs/summary");
        setSummary(s);
        await showSuccess("Import Completed", d.message || "Historical analytics data imported successfully.");
        if (onImportSuccess) onImportSuccess();
      } else {
        await showError("Import Failed", d.message || "Upload failed.");
      }
    } catch (error) {
      const rawMessage = error?.message || "";
      const message =
        /Unexpected token|not valid JSON/i.test(rawMessage)
          ? "The server returned an unexpected response while importing. Please refresh the page and try again."
          : getErrorMessage(
              error,
              "The import could not be completed right now. Please try again."
            );
      setResult({ success: false, message });
      await showError("Import Failed", message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }
  async function deleteBatch(id) {
    const confirmed = await confirmAction({
      title: "Delete Import Batch?",
      text: "This will remove the selected imported analytics batch.",
      confirmButtonText: "Delete",
      confirmButtonColor: "#dc3545"
    });
    if (!confirmed) return;
    setResult(null);
    try {
      const response = await fetchJson(`/api/import-logs/delete/${id}`, {
        method: "POST",
      });
      const s = await fetchJson("/api/import-logs/summary");
      setSummary(s);
      setResult({
        success: true,
        message: `Deleted import batch successfully.`,
        deleted: response?.deleted ?? 0,
      });
      await showSuccess("Batch Deleted", "The imported analytics batch was deleted successfully.");
      if (onImportSuccess) onImportSuccess();
    } catch (error) {
      const status = error?.status;
      const message =
        status === 403
          ? "You are not allowed to delete imported data."
          : error?.data?.message || error?.message || "Delete failed.";
      setResult({ success: false, message });
      await showError("Delete Failed", message);
    }
  }
  const live = summary?.live_logs || 0,
    imported = summary?.total_imported || 0;

  return (
    <>
      <button
        className="btn btn-sm btn-outline-primary d-flex align-items-center gap-1 px-2 py-1"
        onClick={() => {
          setResult(null);
          setShowModal(true);
        }}
      >
        <i className="bi bi-upload" style={{ fontSize: 12 }}></i> Import Data
      </button>
      {showModal && typeof document !== "undefined" && createPortal((
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 2000,
            background: "rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "84px 16px 24px",
            backdropFilter: "blur(3px)",
            overflowY: "auto",
          }}
          onClick={(e) => e.target === e.currentTarget && setShowModal(false)}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: 16,
              width: "100%",
              maxWidth: 460,
              boxShadow: "0 20px 60px rgba(0,0,0,0.15)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                padding: "18px 22px 14px",
                borderBottom: "1px solid #f0f0f0",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
              }}
            >
              <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                <div
                  style={{
                    width: 36,
                    height: 36,
                    borderRadius: 10,
                    background: "rgba(13,110,253,0.08)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <i
                    className="bi bi-cloud-upload text-primary"
                    style={{ fontSize: 17 }}
                  ></i>
                </div>
                <div>
                  <div
                    style={{ fontWeight: 700, fontSize: 14, color: "#1a1a2e" }}
                  >
                    Import Historical Data
                  </div>
                  <div style={{ fontSize: 11, color: "#aaa" }}>
                    Analytics baseline only · Kiosk unaffected
                  </div>
                </div>
              </div>
              <button
                onClick={() => setShowModal(false)}
                style={{
                  background: "none",
                  border: "none",
                  cursor: "pointer",
                  color: "#aaa",
                  fontSize: 17,
                  padding: "2px 6px",
                }}
              >
                <i className="bi bi-x-lg"></i>
              </button>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr 1fr",
                borderBottom: "1px solid #f0f0f0",
              }}
            >
              {[
                ["Live", live, "#198754"],
                ["Imported", imported, "#0d6efd"],
                ["Total", live + imported, "#1a1a2e"],
              ].map(([l, v, c], i) => (
                <div
                  key={i}
                  style={{
                    padding: "10px 16px",
                    textAlign: "center",
                    background: "#fff",
                  }}
                >
                  <div style={{ fontSize: 17, fontWeight: 700, color: c }}>
                    {fmt(v)}
                  </div>
                  <div style={{ fontSize: 10.5, color: "#aaa" }}>{l}</div>
                </div>
              ))}
            </div>
            <div style={{ padding: "18px 22px" }}>
              <div
                style={{
                  background: "#f8f9fa",
                  borderRadius: 8,
                  padding: "8px 12px",
                  fontSize: 11.5,
                  marginBottom: 14,
                }}
              >
                <span style={{ fontWeight: 600, color: "#333" }}>
                  <i className="bi bi-info-circle text-primary me-1"></i>
                  Columns:
                </span>
                <code style={{ color: "#0d6efd", marginLeft: 6, fontSize: 11 }}>
                  sr_code, name, gender, program, year_level, timestamp
                </code>
              </div>
              <div
                onClick={() => !uploading && fileInputRef.current?.click()}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragging(false);
                  uploadFile(e.dataTransfer.files?.[0]);
                }}
                style={{
                  border: `2px dashed ${dragging ? "#0d6efd" : "#dde"}`,
                  borderRadius: 12,
                  padding: "28px 20px",
                  textAlign: "center",
                  cursor: uploading ? "not-allowed" : "pointer",
                  background: dragging ? "rgba(13,110,253,0.03)" : "#fafbfc",
                  transition: "all 0.2s",
                  marginBottom: 14,
                }}
              >
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv"
                  style={{ display: "none" }}
                  onChange={(e) => uploadFile(e.target.files?.[0])}
                />
                {uploading ? (
                  <>
                    <div className="spinner-border spinner-border-sm text-primary mb-2"></div>
                    <div style={{ fontSize: 12.5, color: "#666" }}>
                      Importing...
                    </div>
                  </>
                ) : (
                  <>
                    <i
                      className="bi bi-file-earmark-arrow-up"
                      style={{
                        fontSize: 28,
                        color: dragging ? "#0d6efd" : "#bbb",
                      }}
                    ></i>
                    <div
                      style={{
                        fontSize: 13,
                        fontWeight: 600,
                        color: "#333",
                        marginTop: 6,
                      }}
                    >
                      Click to upload or drag & drop
                    </div>
                    <div style={{ fontSize: 11, color: "#bbb", marginTop: 2 }}>
                      CSV files only
                    </div>
                  </>
                )}
              </div>
              {result && (
                <div
                  style={{
                    borderRadius: 8,
                    padding: "9px 12px",
                    marginBottom: 14,
                    fontSize: 12.5,
                    background: result.success
                      ? "rgba(25,135,84,0.07)"
                      : "rgba(220,53,69,0.07)",
                    border: `1px solid ${result.success ? "rgba(25,135,84,0.2)" : "rgba(220,53,69,0.2)"}`,
                  }}
                >
                  <div
                    style={{
                      fontWeight: 600,
                      color: result.success ? "#198754" : "#dc3545",
                    }}
                  >
                    <i
                      className={`bi bi-${result.success ? "check-circle" : "x-circle"} me-1`}
                    ></i>
                    {result.message}
                  </div>
                  {result.success && (
                    <div style={{ color: "#555", marginTop: 2 }}>
                      {typeof result.deleted === "number"
                        ? `${result.deleted} deleted`
                        : `${result.inserted} imported${result.skipped > 0 ? ` · ${result.skipped} skipped` : ""}`}
                    </div>
                  )}
                </div>
              )}
              {summary?.batches?.length > 0 && (
                <>
                  <button
                    className="btn btn-link btn-sm p-0 text-muted text-decoration-none"
                    onClick={() => setShowHistory((v) => !v)}
                    style={{ fontSize: 11.5 }}
                  >
                    <i
                      className={`bi bi-chevron-${showHistory ? "up" : "down"} me-1`}
                    ></i>
                    {showHistory ? "Hide" : "Show"} history (
                    {summary.batches.length})
                  </button>
                  {showHistory && (
                    <div style={{ marginTop: 8 }}>
                      {summary.batches.map((b, i) => (
                        <div
                          key={i}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "space-between",
                            padding: "7px 10px",
                            background: "#f8f9fa",
                            borderRadius: 7,
                            marginBottom: 5,
                            fontSize: 11.5,
                          }}
                        >
                          <div>
                            <span className="badge bg-primary me-2">
                              {b.count}
                            </span>
                            <span style={{ color: "#666" }}>
                              {b.earliest} → {b.latest}
                            </span>
                          </div>
                          <div
                            style={{
                              display: "flex",
                              gap: 8,
                              alignItems: "center",
                            }}
                          >
                            <span style={{ color: "#bbb" }}>
                              {b.imported_at}
                            </span>
                            <button
                              onClick={() => deleteBatch(b.batch_id)}
                              style={{
                                background: "none",
                                border: "none",
                                color: "#dc3545",
                                cursor: "pointer",
                                fontSize: 13,
                              }}
                            >
                              <i className="bi bi-trash"></i>
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </>
              )}
            </div>
            <div
              style={{
                padding: "10px 22px",
                borderTop: "1px solid #f0f0f0",
                background: "#fafafa",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <span style={{ fontSize: 11, color: "#bbb" }}>
                <i className="bi bi-shield-check text-success me-1"></i>
                Analytics only · Never writes to kiosk tables
              </span>
              <button
                className="btn btn-sm btn-secondary"
                onClick={() => setShowModal(false)}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      ), document.body)}
    </>
  );
}

// ── Pipeline Stepper ──────────────────────────────────────────
function PipelineStepper({ activeStep, isLoading = false }) {
  const steps = [
    { n: 1, label: "Merge Sources", icon: "bi-layers" },
    { n: 2, label: "Clean Data", icon: "bi-funnel" },
    { n: 3, label: "Transform", icon: "bi-arrow-repeat" },
    { n: 4, label: "EDA", icon: "bi-bar-chart-line" },
    { n: 5, label: "Model", icon: "bi-cpu" },
    { n: 6, label: "Report", icon: "bi-file-earmark-bar-graph" },
  ];
  return (
    <>
      <style>{`
        @keyframes pipelineSpin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes pipelinePulse { 0% { box-shadow: 0 0 0 0 rgba(13,110,253,0.18); } 70% { box-shadow: 0 0 0 10px rgba(13,110,253,0); } 100% { box-shadow: 0 0 0 0 rgba(13,110,253,0); } }
      `}</style>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 0,
          overflowX: "auto",
          padding: "4px 0",
        }}
      >
        {steps.map((s, i) => {
          const done = s.n < activeStep,
            current = s.n === activeStep;
          return (
            <React.Fragment key={s.n}>
              <div
                style={{
                  display: "flex",
                  flexDirection: "column",
                  alignItems: "center",
                  minWidth: 90,
                  flex: 1,
                }}
              >
                <div
                  style={{
                    width: 38,
                    height: 38,
                    borderRadius: "50%",
                    background: done
                      ? "#198754"
                      : current
                        ? "#0d6efd"
                        : "#e9ecef",
                    color: done || current ? "#fff" : "#adb5bd",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 15,
                    fontWeight: 700,
                    boxShadow: current
                      ? "0 0 0 4px rgba(13,110,253,0.15)"
                      : "none",
                    animation:
                      isLoading && current
                        ? "pipelinePulse 1.2s ease-out infinite"
                        : "none",
                    transition: "all 0.3s",
                  }}
                >
                  {done ? (
                    <i className="bi bi-check-lg"></i>
                  ) : isLoading && current ? (
                    <i
                      className="bi bi-arrow-repeat"
                      style={{ animation: "pipelineSpin 1s linear infinite" }}
                    ></i>
                  ) : (
                    <i className={s.icon}></i>
                  )}
                </div>
                <div
                  style={{
                    fontSize: 10.5,
                    fontWeight: current ? 700 : 400,
                    color: done ? "#198754" : current ? "#0d6efd" : "#adb5bd",
                    marginTop: 5,
                    textAlign: "center",
                    lineHeight: 1.2,
                  }}
                >
                  {s.label}
                </div>
              </div>
              {i < steps.length - 1 && (
                <div
                  style={{
                    flex: 1,
                    height: 2,
                    minWidth: 16,
                    background: done
                      ? "#198754"
                      : isLoading && current
                        ? "linear-gradient(90deg, #0d6efd 0%, #86b7fe 100%)"
                        : "#e9ecef",
                    marginBottom: 20,
                    transition: "background 0.3s",
                  }}
                ></div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </>
  );
}

// ── Collapsible Section ───────────────────────────────────────
function Section({
  stepNum,
  title,
  subtitle,
  color = "#0d6efd",
  defaultOpen = true,
  children,
}) {
  const [open, setOpen] = React.useState(defaultOpen);
  return (
    <div
      style={{
        background: "#fff",
        borderRadius: 12,
        border: "1px solid #e9ecef",
        overflow: "hidden",
        marginBottom: 16,
        boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
      }}
    >
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          width: "100%",
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: "14px 20px",
          display: "flex",
          alignItems: "center",
          gap: 12,
          textAlign: "left",
          borderBottom: open ? "1px solid #f0f2f5" : "none",
        }}
      >
        <div
          style={{
            width: 28,
            height: 28,
            borderRadius: 8,
            background: color + "18",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontWeight: 800,
            fontSize: 12,
            color,
            flexShrink: 0,
          }}
        >
          {stepNum}
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 700, fontSize: 14, color: "#1a1a2e" }}>
            {title}
          </div>
          {subtitle && (
            <div style={{ fontSize: 11.5, color: "#aaa", marginTop: 1 }}>
              {subtitle}
            </div>
          )}
        </div>
        <i
          className={`bi bi-chevron-${open ? "up" : "down"}`}
          style={{ color: "#bbb", fontSize: 13 }}
        ></i>
      </button>
      {open && <div style={{ padding: "18px 20px" }}>{children}</div>}
    </div>
  );
}

// ── Data Quality Section ──────────────────────────────────────
function DataQualitySection({ dq }) {
  if (!dq) return null;
  const pct = dq.quality_score;
  const color = pct >= 90 ? "#198754" : pct >= 70 ? "#ffc107" : "#dc3545";
  const steps = [
    {
      label: "Raw records",
      value: dq.total_raw,
      icon: "bi-database",
      color: "#6c757d",
      sub: `Live: ${fmt(dq.total_live)} · Imported: ${fmt(dq.total_imported)}`,
    },
    {
      label: "Removed — low confidence",
      value: dq.removed_low_conf,
      icon: "bi-shield-x",
      color: "#dc3545",
      sub: "Below 50% confidence (live logs only)",
    },
    {
      label: "Removed — outside hours",
      value: dq.removed_outside_hrs,
      icon: "bi-clock-history",
      color: "#fd7e14",
      sub: "Before 7AM or after 7PM",
    },
    {
      label: "Removed — duplicates",
      value: dq.removed_duplicates,
      icon: "bi-copy",
      color: "#e8a840",
      sub: "Kept first scan per student per day",
    },
    {
      label: "Clean records",
      value: dq.total_cleaned,
      icon: "bi-check-circle-fill",
      color: "#198754",
      sub: "Ready for analysis",
    },
  ];
  const dominant =
    dq.removed_duplicates > dq.removed_outside_hrs
      ? "duplicate scans"
      : "out-of-hours entries";
  const dominantCount = Math.max(dq.removed_duplicates, dq.removed_outside_hrs);

  return (
    <>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4,1fr)",
          gap: 12,
          marginBottom: 20,
        }}
      >
        {[
          { label: "Live Raw", value: fmt(dq.total_live), accent: "#0d6efd" },
          {
            label: "Imported Raw",
            value: fmt(dq.total_imported),
            accent: "#6f42c1",
          },
          {
            label: "Cleaned Output",
            value: fmt(dq.total_cleaned),
            accent: "#198754",
          },
          {
            label: "Quality Score",
            value: `${pct}%`,
            accent: color,
            highlight: true,
          },
        ].map((m, i) => (
          <div
            key={i}
            style={{
              borderRadius: 10,
              border: `1px solid ${m.highlight ? color + "40" : "#e9ecef"}`,
              padding: "14px 16px",
              background: m.highlight ? color + "08" : "#fafbfc",
            }}
          >
            <div style={{ fontSize: 11, color: "#aaa", marginBottom: 4 }}>
              {m.label}
            </div>
            <div style={{ fontSize: 22, fontWeight: 800, color: m.accent }}>
              {m.value}
            </div>
          </div>
        ))}
      </div>
      <div style={{ marginBottom: 20 }}>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            fontSize: 11.5,
            color: "#888",
            marginBottom: 6,
          }}
        >
          <span>Data retained after cleaning</span>
          <span style={{ fontWeight: 700, color }}>{pct}%</span>
        </div>
        <div
          style={{
            height: 8,
            background: "#e9ecef",
            borderRadius: 99,
            overflow: "hidden",
          }}
        >
          <div
            style={{
              height: "100%",
              width: `${pct}%`,
              background: color,
              borderRadius: 99,
              transition: "width 1s ease",
            }}
          ></div>
        </div>
      </div>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 8,
          marginBottom: 4,
        }}
      >
        {steps.map((s, i) => (
          <div
            key={i}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              padding: "10px 14px",
              background: "#f8f9fa",
              borderRadius: 8,
              border: "1px solid #f0f0f0",
            }}
          >
            <div
              style={{
                width: 32,
                height: 32,
                borderRadius: 8,
                background: s.color + "15",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                flexShrink: 0,
              }}
            >
              <i
                className={`bi ${s.icon}`}
                style={{ color: s.color, fontSize: 14 }}
              ></i>
            </div>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13, fontWeight: 500, color: "#333" }}>
                {s.label}
              </div>
              <div style={{ fontSize: 11, color: "#aaa" }}>{s.sub}</div>
            </div>
            <div style={{ textAlign: "right", flexShrink: 0 }}>
              <div
                style={{
                  fontSize: 15,
                  fontWeight: 700,
                  color:
                    i === 0
                      ? "#6c757d"
                      : i === steps.length - 1
                        ? "#198754"
                        : "#dc3545",
                }}
              >
                {i === 0
                  ? fmt(s.value)
                  : i === steps.length - 1
                    ? fmt(s.value)
                    : s.value > 0
                      ? `−${fmt(s.value)}`
                      : "0"}
              </div>
              {dq.total_raw > 0 && (
                <div style={{ fontSize: 10.5, color: "#bbb" }}>
                  {((s.value / dq.total_raw) * 100).toFixed(1)}%
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <Interpretation icon="bi-lightbulb" color="#ffc107">
        <strong>Data Quality Interpretation:</strong> Out of{" "}
        <strong>{fmt(dq.total_raw)}</strong> total raw records,{" "}
        <strong>{fmt(dq.total_cleaned)}</strong> ({pct}%) were retained after
        cleaning. The most significant source of removal was{" "}
        <strong>{dominant}</strong> ({fmt(dominantCount)} records), which is
        expected in a continuous face recognition system. A quality score of{" "}
        <strong style={{ color }}>{pct}%</strong>{" "}
        {pct >= 90
          ? "indicates high data integrity and reliable analytics results."
          : pct >= 70
            ? "indicates acceptable data quality with minor concerns."
            : "indicates data quality issues that may affect analytics reliability."}
      </Interpretation>
    </>
  );
}

// ── EDA Section ───────────────────────────────────────────────
function EDASection({ stats, dowLabels, dowAverages }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !dowAverages?.length) return;
    if (chartRef.current) chartRef.current.destroy();
    const max = Math.max(...dowAverages, 1);
    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels: dowLabels,
        datasets: [
          {
            data: dowAverages,
            backgroundColor: dowAverages.map((v) => {
              const t = v / max;
              return t >= 0.75
                ? "rgba(220,53,69,0.75)"
                : t >= 0.5
                  ? "rgba(255,193,7,0.75)"
                  : "rgba(13,110,253,0.55)";
            }),
            borderRadius: 6,
            borderWidth: 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: { label: (ctx) => ` ${ctx.parsed.y} avg visits` },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: {
            beginAtZero: true,
            grid: { color: "rgba(0,0,0,0.04)" },
            ticks: { font: { size: 11 } },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [dowAverages]);

  if (!stats) return null;
  const busiestDayIdx = dowAverages
    ? dowAverages.indexOf(Math.max(...dowAverages))
    : -1;
  const busiestDay = dowLabels?.[busiestDayIdx] ?? "—";
  const quietestDayIdx = dowAverages
    ? dowAverages.indexOf(Math.min(...dowAverages))
    : -1;
  const quietestDay = dowLabels?.[quietestDayIdx] ?? "—";
  const isConsistent = stats.std_dev < stats.mean_daily_visits * 0.3;

  const metrics = [
    { label: "Mean/Day", value: stats.mean_daily_visits, color: "#0d6efd" },
    { label: "Median/Day", value: stats.median_daily_visits, color: "#6f42c1" },
    { label: "Max Day", value: stats.max_daily_visits, color: "#198754" },
    { label: "Min Day", value: stats.min_daily_visits, color: "#fd7e14" },
    { label: "Std Dev", value: stats.std_dev, color: "#dc3545" },
    { label: "Active Days", value: stats.total_visit_days, color: "#20c997" },
  ];

  return (
    <div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 20,
          marginBottom: 4,
        }}
      >
        <div>
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#555",
              marginBottom: 10,
            }}
          >
            Descriptive Statistics
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "1fr 1fr 1fr",
              gap: 10,
              marginBottom: 14,
            }}
          >
            {metrics.map((m, i) => (
              <div
                key={i}
                style={{
                  background: "#f8f9fa",
                  borderRadius: 10,
                  padding: "12px 14px",
                  border: "1px solid #f0f0f0",
                }}
              >
                <div style={{ fontSize: 10.5, color: "#aaa", marginBottom: 3 }}>
                  {m.label}
                </div>
                <div style={{ fontSize: 20, fontWeight: 800, color: m.color }}>
                  {m.value}
                </div>
              </div>
            ))}
          </div>
          <Interpretation icon="bi-bar-chart-line" color="#0dcaf0">
            <strong>Descriptive Statistics Interpretation:</strong> The library
            receives an average of <strong>{stats.mean_daily_visits}</strong>{" "}
            unique student visits per day (median:{" "}
            <strong>{stats.median_daily_visits}</strong>). The standard
            deviation of <strong>{stats.std_dev}</strong> suggests{" "}
            {isConsistent
              ? "consistent and predictable daily attendance patterns."
              : "notable variability in daily visits, likely influenced by academic calendar events such as exam weeks or holidays."}{" "}
            Peak attendance reached <strong>{stats.max_daily_visits}</strong>{" "}
            visits in a single day, while the lowest recorded was{" "}
            <strong>{stats.min_daily_visits}</strong>.
          </Interpretation>
        </div>
        <div>
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#555",
              marginBottom: 10,
            }}
          >
            Average Visits by Day of Week
          </div>
          <div style={{ height: 200, position: "relative", marginBottom: 4 }}>
            <canvas ref={canvasRef}></canvas>
          </div>
          <Interpretation icon="bi-calendar-week" color="#6f42c1">
            <strong>Day-of-Week Chart Interpretation:</strong>{" "}
            <strong style={{ color: "#dc3545" }}>{busiestDay}</strong> is
            consistently the busiest day while{" "}
            <strong style={{ color: "#198754" }}>{quietestDay}</strong> records
            the lowest average attendance. Library management can use this to
            optimize staff scheduling. Saturday and Sunday should be read as
            non-operating or closed-library days, so low weekend averages are
            expected.
          </Interpretation>
        </div>
      </div>
    </div>
  );
}

// ── Trend Chart ───────────────────────────────────────────────
function TrendChart({ labels, counts }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const recentMean = React.useMemo(() => {
    if (!counts?.length) return 0;
    return Number(
      (counts.reduce((s, v) => s + v, 0) / counts.length).toFixed(1),
    );
  }, [counts]);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !counts?.length) return;
    if (chartRef.current) chartRef.current.destroy();
    chartRef.current = new window.Chart(canvasRef.current, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Daily Visits",
            data: counts,
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,0.06)",
            borderWidth: 2,
            pointRadius: 3,
            fill: true,
            tension: 0.3,
          },
          {
            label: "30-Day Mean",
            data: Array(counts.length).fill(recentMean),
            borderColor: "rgba(220,53,69,0.5)",
            borderWidth: 1.5,
            borderDash: [6, 4],
            pointRadius: 0,
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "top",
            labels: { font: { size: 11 }, boxWidth: 12, padding: 16 },
          },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.parsed.y} visits` } },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxTicksLimit: 10, font: { size: 10 } },
          },
          y: {
            beginAtZero: true,
            grid: { color: "rgba(0,0,0,0.04)" },
            ticks: { font: { size: 11 } },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [labels, counts, recentMean]);

  if (!counts?.length) return null;
  const aboveMean = counts.filter((v) => v > recentMean).length;
  const belowMean = counts.filter((v) => v < recentMean).length;
  const trend =
    counts.length > 7
      ? counts.slice(-7).reduce((a, b) => a + b, 0) / 7 >
        counts.slice(0, 7).reduce((a, b) => a + b, 0) / 7
        ? "upward"
        : "downward"
      : "stable";

  return (
    <div>
      <div style={{ height: 220, position: "relative", marginBottom: 4 }}>
        <canvas ref={canvasRef}></canvas>
      </div>
      <Interpretation icon="bi-graph-up" color="#0d6efd">
        <strong>30-Day Trend Interpretation:</strong> Over the last 30 days, the
        library shows a{" "}
        <strong
          style={{
            color:
              trend === "upward"
                ? "#198754"
                : trend === "downward"
                  ? "#dc3545"
                  : "#ffc107",
          }}
        >
          {trend} trend
        </strong>{" "}
        in daily visits. <strong>{aboveMean}</strong> days recorded
        above-average attendance while <strong>{belowMean}</strong> days fell
        below the 30-day mean of <strong>{recentMean}</strong> visits/day.{" "}
        Weekend dips should be interpreted carefully — Saturday and Sunday are
        not regular library-open days.{" "}
        {trend === "upward"
          ? "This suggests growing student engagement with the library over time."
          : trend === "downward"
            ? "This may indicate reduced library usage and could warrant attention from management."
            : "Attendance is relatively stable without strong growth or decline."}
      </Interpretation>
    </div>
  );
}

// ── Forecast Section ──────────────────────────────────────────
function ForecastSection({
  forecast,
  allForecasts,
  comparison,
  bestModel,
  comparisonInterp,
}) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const [activeModel, setActiveModel] = React.useState(null);

  // Use active model's data or fall back to primary forecast
  const displayForecast = React.useMemo(() => {
    if (activeModel && allForecasts?.length) {
      return allForecasts.find((f) => f.model === activeModel) || forecast;
    }
    return forecast;
  }, [activeModel, allForecasts, forecast]);

  const MODEL_COLORS = {
    ARIMA: "#0d6efd",
    SARIMA: "#6f42c1",
    Prophet: "#198754",
    "Holt-Winters": "#fd7e14",
    "Moving Average": "#6c757d",
  };

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !displayForecast?.values?.length)
      return;
    if (chartRef.current) chartRef.current.destroy();

    const color = MODEL_COLORS[displayForecast.model] || "#0d6efd";

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels: displayForecast.labels,
        datasets: [
          {
            label: `${displayForecast.model} — Predicted`,
            data: displayForecast.values,
            backgroundColor: color + "bb",
            borderRadius: 8,
            borderWidth: 0,
            order: 2,
          },
          {
            label: "Upper (95%)",
            data: displayForecast.upper,
            type: "line",
            borderColor: "rgba(220,53,69,0.45)",
            borderWidth: 1.5,
            borderDash: [4, 3],
            pointRadius: 0,
            fill: false,
            order: 1,
          },
          {
            label: "Lower (95%)",
            data: displayForecast.lower,
            type: "line",
            borderColor: "rgba(25,135,84,0.45)",
            borderWidth: 1.5,
            borderDash: [4, 3],
            pointRadius: 0,
            fill: false,
            order: 1,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "top",
            labels: { font: { size: 11 }, boxWidth: 12, padding: 14 },
          },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.parsed.y} visits` } },
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.04)" } },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [displayForecast]);

  if (!forecast?.values?.length)
    return (
      <div
        style={{
          textAlign: "center",
          color: "#aaa",
          padding: "32px 0",
          fontSize: 13,
        }}
      >
        <i
          className="bi bi-hourglass"
          style={{ fontSize: 28, display: "block", marginBottom: 8 }}
        ></i>
        Not enough data to forecast. Need at least 7 days of records.
      </div>
    );

  const peakDay =
    displayForecast.labels?.[
      displayForecast.values?.indexOf(
        Math.max(...(displayForecast.values || [0])),
      )
    ] ?? "—";
  const quietDay =
    displayForecast.labels?.[
      displayForecast.values?.indexOf(
        Math.min(...(displayForecast.values || [0])),
      )
    ] ?? "—";
  const totalWeek = (displayForecast.values || []).reduce((a, b) => a + b, 0);

  return (
    <div>
      {/* ── Best model banner ── */}
      {bestModel && (
        <div
          style={{
            background: "rgba(25,135,84,0.07)",
            border: "1px solid rgba(25,135,84,0.2)",
            borderRadius: 8,
            padding: "8px 14px",
            marginBottom: 14,
            fontSize: 12.5,
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <i className="bi bi-trophy-fill" style={{ color: "#198754" }}></i>
          <span>
            Best performing model:{" "}
            <strong style={{ color: "#198754" }}>{bestModel}</strong> — selected
            by lowest RMSE on back-test
          </span>
        </div>
      )}

      {/* ── Model selector tabs ── */}
      {allForecasts?.length > 0 && (
        <div
          style={{
            display: "flex",
            gap: 6,
            marginBottom: 14,
            flexWrap: "wrap",
          }}
        >
          {allForecasts.map((f) => {
            const isActive = (activeModel || bestModel) === f.model;
            const color = MODEL_COLORS[f.model] || "#0d6efd";
            return (
              <button
                key={f.model}
                onClick={() => setActiveModel(f.model)}
                style={{
                  padding: "6px 14px",
                  borderRadius: 20,
                  fontSize: 12,
                  fontWeight: 500,
                  border: `1px solid ${isActive ? color : color + "40"}`,
                  background: isActive ? color + "15" : "#f8f9fa",
                  color: isActive ? color : "#666",
                  cursor: "pointer",
                  transition: "all 0.15s",
                }}
              >
                {f.model === bestModel && (
                  <i
                    className="bi bi-trophy-fill me-1"
                    style={{ fontSize: 10 }}
                  ></i>
                )}
                {f.model}
              </button>
            );
          })}
        </div>
      )}

      {/* ── Active model method label ── */}
      <div
        style={{
          background: "rgba(255,193,7,0.08)",
          border: "1px solid rgba(255,193,7,0.2)",
          borderRadius: 8,
          padding: "8px 14px",
          marginBottom: 14,
          fontSize: 12,
        }}
      >
        <i className="bi bi-cpu text-warning me-1"></i>
        <strong>Method:</strong> {displayForecast.method}
        {displayForecast.aic && (
          <span style={{ marginLeft: 12, color: "#888" }}>
            AIC: <strong>{displayForecast.aic}</strong> · BIC:{" "}
            <strong>{displayForecast.bic}</strong>
          </span>
        )}
        {displayForecast.params &&
          Object.keys(displayForecast.params).length > 0 && (
            <span style={{ marginLeft: 12, color: "#888" }}>
              α={displayForecast.params.alpha} · β={displayForecast.params.beta}{" "}
              · γ={displayForecast.params.gamma}
            </span>
          )}
      </div>

      {/* ── Chart ── */}
      <div style={{ height: 220, position: "relative", marginBottom: 16 }}>
        <canvas ref={canvasRef}></canvas>
      </div>

      {/* ── Forecast table ── */}
      <div style={{ overflowX: "auto", marginBottom: 16 }}>
        <table
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}
        >
          <thead>
            <tr style={{ background: "#f8f9fa" }}>
              {["Day", "Predicted", "95% CI Range"].map((h, i) => (
                <th
                  key={i}
                  style={{
                    padding: "8px 12px",
                    textAlign: "center",
                    fontWeight: 600,
                    color: "#555",
                    border: "1px solid #e9ecef",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {displayForecast.labels?.map((l, i) => (
              <tr key={i}>
                <td
                  style={{
                    padding: "7px 12px",
                    textAlign: "center",
                    border: "1px solid #e9ecef",
                    color: "#555",
                  }}
                >
                  {l}
                </td>
                <td
                  style={{
                    padding: "7px 12px",
                    textAlign: "center",
                    border: "1px solid #e9ecef",
                    fontWeight: 700,
                    color: MODEL_COLORS[displayForecast.model] || "#0d6efd",
                  }}
                >
                  {displayForecast.values?.[i]}
                </td>
                <td
                  style={{
                    padding: "7px 12px",
                    textAlign: "center",
                    border: "1px solid #e9ecef",
                    color: "#aaa",
                  }}
                >
                  {displayForecast.lower?.[i]} – {displayForecast.upper?.[i]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* ── Model comparison table ── */}
      {comparison?.length > 0 && (
        <div style={{ marginBottom: 14 }}>
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#555",
              marginBottom: 8,
            }}
          >
            Model Comparison — Back-test on Last 7 Days
          </div>
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 12.5,
              }}
            >
              <thead>
                <tr style={{ background: "#f8f9fa" }}>
                  {[
                    "Model",
                    "MAE",
                    "RMSE",
                    "MAPE (%)",
                    "7-Day Total",
                    "AIC",
                  ].map((h, i) => (
                    <th
                      key={i}
                      style={{
                        padding: "8px 12px",
                        textAlign: i === 0 ? "left" : "center",
                        fontWeight: 600,
                        color: "#555",
                        border: "1px solid #e9ecef",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {comparison.map((row, i) => {
                  const isBest = row.model === bestModel;
                  const color = MODEL_COLORS[row.model] || "#0d6efd";
                  return (
                    <tr
                      key={i}
                      style={{
                        background: isBest
                          ? color + "08"
                          : i % 2 === 0
                            ? "#fff"
                            : "#fafafa",
                        border: isBest ? `1px solid ${color}30` : "none",
                      }}
                    >
                      <td
                        style={{
                          padding: "8px 12px",
                          border: "1px solid #f0f0f0",
                          fontWeight: isBest ? 700 : 500,
                        }}
                      >
                        <span
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: 6,
                          }}
                        >
                          <span
                            style={{
                              width: 8,
                              height: 8,
                              borderRadius: "50%",
                              background: color,
                              display: "inline-block",
                              flexShrink: 0,
                            }}
                          ></span>
                          {row.model}
                          {isBest && (
                            <span
                              style={{
                                background: color + "20",
                                color,
                                fontSize: 10,
                                padding: "1px 6px",
                                borderRadius: 10,
                                fontWeight: 600,
                              }}
                            >
                              Best
                            </span>
                          )}
                        </span>
                      </td>
                      <td
                        style={{
                          padding: "8px 12px",
                          textAlign: "center",
                          border: "1px solid #f0f0f0",
                          color: isBest ? "#198754" : "#555",
                          fontWeight: isBest ? 700 : 400,
                        }}
                      >
                        {row.mae ?? "—"}
                      </td>
                      <td
                        style={{
                          padding: "8px 12px",
                          textAlign: "center",
                          border: "1px solid #f0f0f0",
                          color: isBest ? "#198754" : "#555",
                          fontWeight: isBest ? 700 : 400,
                        }}
                      >
                        {row.rmse ?? "—"}
                      </td>
                      <td
                        style={{
                          padding: "8px 12px",
                          textAlign: "center",
                          border: "1px solid #f0f0f0",
                          color: isBest ? "#198754" : "#555",
                          fontWeight: isBest ? 700 : 400,
                        }}
                      >
                        {row.mape != null ? `${row.mape}%` : "—"}
                      </td>
                      <td
                        style={{
                          padding: "8px 12px",
                          textAlign: "center",
                          border: "1px solid #f0f0f0",
                        }}
                      >
                        <span
                          style={{
                            background: color + "15",
                            color,
                            padding: "2px 8px",
                            borderRadius: 20,
                            fontWeight: 700,
                            fontSize: 12,
                          }}
                        >
                          {row.total_7d}
                        </span>
                      </td>
                      <td
                        style={{
                          padding: "8px 12px",
                          textAlign: "center",
                          border: "1px solid #f0f0f0",
                          color: "#aaa",
                        }}
                      >
                        {row.aic ?? "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Interpretation ── */}
      <Interpretation icon="bi-graph-up-arrow" color="#ffc107">
        <strong>Forecast Interpretation ({displayForecast.model}):</strong>{" "}
        {displayForecast.interpretation} Peak forecasted day:{" "}
        <strong style={{ color: "#dc3545" }}>{peakDay}</strong> ({totalWeek}{" "}
        total visits predicted). Forecasted Saturday/Sunday values reflect
        expected lower traffic on non-operating days.
      </Interpretation>

      {comparisonInterp && (
        <Interpretation icon="bi-bar-chart-steps" color="#0d6efd">
          <strong>Model Comparison Interpretation:</strong>{" "}
          <span dangerouslySetInnerHTML={{ __html: comparisonInterp }} />
        </Interpretation>
      )}
    </div>
  );
}

// ── Linear Regression Section ─────────────────────────────────
function LinearRegressionSection({
  regression,
  interpretation,
  counts,
  labels,
}) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const chartLabels = labels || regression?.labels || [];
  const actualCounts =
    counts?.length === chartLabels.length
      ? counts
      : regression?.counts?.length === chartLabels.length
        ? regression.counts
        : [];
  const hasRegressionData =
    Array.isArray(regression?.fitted) &&
    regression.fitted.length >= 2 &&
    chartLabels.length === regression.fitted.length;

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !hasRegressionData) return;
    if (chartRef.current) chartRef.current.destroy();
    chartRef.current = new window.Chart(canvasRef.current, {
      type: "line",
      data: {
        labels: chartLabels,
        datasets: [
          {
            label: "Actual visits",
            data: actualCounts,
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,0.06)",
            borderWidth: 2,
            pointRadius: 2,
            fill: true,
            tension: 0.3,
          },
          {
            label: `Trend line (R²=${regression.r2 ?? "—"})`,
            data: regression.fitted,
            borderColor: "#dc3545",
            borderWidth: 2,
            borderDash: [5, 3],
            pointRadius: 0,
            fill: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: {
            position: "top",
            labels: { font: { size: 11 }, boxWidth: 12, padding: 14 },
          },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.parsed.y} visits` } },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxTicksLimit: 10, font: { size: 10 } },
          },
          y: { beginAtZero: true, grid: { color: "rgba(0,0,0,0.04)" } },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [actualCounts, chartLabels, hasRegressionData, regression]);

  if (!hasRegressionData || regression?.r2 == null)
    return (
      <div
        style={{
          textAlign: "center",
          color: "#aaa",
          padding: "24px 0",
          fontSize: 13,
        }}
      >
        <i
          className="bi bi-hourglass"
          style={{ fontSize: 24, display: "block", marginBottom: 8 }}
        ></i>
        Not enough data for regression analysis.
      </div>
    );

  const trendColor =
    regression.trend === "increasing"
      ? "#198754"
      : regression.trend === "decreasing"
        ? "#dc3545"
        : "#ffc107";

  return (
    <div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4,minmax(0,1fr))",
          gap: 10,
          marginBottom: 16,
        }}
      >
        {[
          {
            label: "R² Score",
            value: regression.r2,
            color: "#0d6efd",
            note: "Variance explained",
          },
          {
            label: "R² (%)",
            value: `${regression.r2_pct}%`,
            color: "#6f42c1",
            note: "Model fit",
          },
          {
            label: "Slope",
            value: regression.slope,
            color: trendColor,
            note: "Visits/day change",
          },
          {
            label: "Trend",
            value: regression.trend,
            color: trendColor,
            note: "Overall direction",
          },
        ].map((m, i) => (
          <div
            key={i}
            style={{
              background: "#f8f9fa",
              borderRadius: 10,
              padding: "12px 14px",
              border: "1px solid #e9ecef",
            }}
          >
            <div style={{ fontSize: 10, color: "#aaa", marginBottom: 2 }}>
              {m.label}
            </div>
            <div
              style={{
                fontSize: 17,
                fontWeight: 700,
                color: m.color,
                textTransform: "capitalize",
              }}
            >
              {m.value}
            </div>
            <div style={{ fontSize: 10, color: "#bbb", marginTop: 1 }}>
              {m.note}
            </div>
          </div>
        ))}
      </div>
      <div style={{ height: 200, position: "relative", marginBottom: 14 }}>
        <canvas ref={canvasRef}></canvas>
      </div>
      <Interpretation icon="bi-graph-up" color="#dc3545">
        <strong>Linear Regression Interpretation:</strong>{" "}
        {interpretation || "No interpretation available."}
      </Interpretation>
    </div>
  );
}

// ── K-Means Clustering Section ────────────────────────────────
function ClusteringSection({ clustering, interpretation }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const [activeIdx, setActiveIdx] = React.useState(0);
  const COLORS = ["#0d6efd", "#198754", "#dc3545", "#ffc107", "#6f42c1"];

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !clustering?.inertia?.length)
      return;
    if (chartRef.current) chartRef.current.destroy();
    chartRef.current = new window.Chart(canvasRef.current, {
      type: "line",
      data: {
        labels: (clustering.k_range || []).map((k) => `k=${k}`),
        datasets: [
          {
            label: "Inertia",
            data: clustering.inertia,
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,0.08)",
            borderWidth: 2,
            pointRadius: 5,
            pointBackgroundColor: "#0d6efd",
            fill: true,
            tension: 0.3,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => ` Inertia: ${Math.round(ctx.parsed.y)}`,
            },
          },
        },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: {
            grid: { color: "rgba(0,0,0,0.04)" },
            ticks: { font: { size: 11 } },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [clustering]);

  if (!clustering?.cluster_summary?.length)
    return (
      <div
        style={{
          textAlign: "center",
          color: "#aaa",
          padding: "24px 0",
          fontSize: 13,
        }}
      >
        <i
          className="bi bi-diagram-3"
          style={{ fontSize: 24, display: "block", marginBottom: 8 }}
        ></i>
        Not enough data for clustering (need at least 3 students).
      </div>
    );

  const summary = clustering.cluster_summary;

  return (
    <div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: 20,
          marginBottom: 20,
        }}
      >
        <div>
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#555",
              marginBottom: 8,
            }}
          >
            Elbow Method — Optimal k Selection
          </div>
          <div style={{ height: 180, position: "relative" }}>
            <canvas ref={canvasRef}></canvas>
          </div>
          <div style={{ fontSize: 11, color: "#aaa", marginTop: 6 }}>
            Selected k ={" "}
            <strong style={{ color: "#0d6efd" }}>{clustering.k}</strong>{" "}
            clusters
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {summary.map((cl, i) => (
            <div
              key={i}
              onClick={() => setActiveIdx(i)}
              style={{
                padding: "12px 14px",
                borderRadius: 8,
                cursor: "pointer",
                background: activeIdx === i ? COLORS[i] + "12" : "#f8f9fa",
                border: `1px solid ${activeIdx === i ? COLORS[i] + "60" : "#e9ecef"}`,
                transition: "all 0.15s",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: "#333" }}>
                    {cl.label}
                  </div>
                  <div style={{ fontSize: 11, color: "#aaa" }}>
                    Avg {cl.avg_visits} visits · {cl.avg_weekly}×/week · peak{" "}
                    {Math.round(cl.avg_hour)}:00
                  </div>
                </div>
                <div
                  style={{ fontSize: 20, fontWeight: 700, color: COLORS[i] }}
                >
                  {cl.count}
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
      {summary[activeIdx] && (
        <div style={{ marginBottom: 14 }}>
          <div
            style={{
              fontSize: 12,
              fontWeight: 600,
              color: "#555",
              marginBottom: 8,
            }}
          >
            {summary[activeIdx].label} — top members
            <span style={{ color: "#aaa", fontWeight: 400, marginLeft: 8 }}>
              Programs: {summary[activeIdx].top_programs?.join(", ")}
            </span>
          </div>
          <div style={{ overflowX: "auto" }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 12.5,
              }}
            >
              <thead>
                <tr style={{ background: "#f8f9fa" }}>
                  {[
                    "#",
                    "Name",
                    "SR Code",
                    "Program",
                    "Total Visits",
                    "Weekly Avg",
                  ].map((h, i) => (
                    <th
                      key={i}
                      style={{
                        padding: "8px 12px",
                        textAlign: "left",
                        fontWeight: 600,
                        color: "#555",
                        border: "1px solid #e9ecef",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {summary[activeIdx].members?.map((m, i) => (
                  <tr
                    key={i}
                    style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}
                  >
                    <td
                      style={{
                        padding: "7px 12px",
                        color: "#bbb",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {i + 1}
                    </td>
                    <td
                      style={{
                        padding: "7px 12px",
                        fontWeight: 500,
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {m.name}
                    </td>
                    <td
                      style={{
                        padding: "7px 12px",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      <code style={{ fontSize: 11 }}>{m.sr_code}</code>
                    </td>
                    <td
                      style={{
                        padding: "7px 12px",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {m.program}
                    </td>
                    <td
                      style={{
                        padding: "7px 12px",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      <span
                        style={{
                          background: COLORS[activeIdx] + "18",
                          color: COLORS[activeIdx],
                          padding: "2px 8px",
                          borderRadius: 20,
                          fontWeight: 700,
                          fontSize: 12,
                        }}
                      >
                        {m.total_visits}
                      </span>
                    </td>
                    <td
                      style={{
                        padding: "7px 12px",
                        color: "#888",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {typeof m.weekly_avg === "number"
                        ? m.weekly_avg.toFixed(2)
                        : m.weekly_avg}
                      ×/wk
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <Interpretation icon="bi-diagram-3" color="#6f42c1">
        <strong>K-Means Clustering Interpretation:</strong>{" "}
        {interpretation || "No interpretation available."}
      </Interpretation>
    </div>
  );
}

// ── Statistical Tests Section ─────────────────────────────────
function StatisticalTestsSection({
  chiSquare,
  chiInterp,
  correlation,
  corrInterp,
  anova,
  anovaInterp,
}) {
  const [activeTest, setActiveTest] = React.useState("chi");

  const tabs = [
    { key: "chi", label: "Chi-square", icon: "bi-grid-3x3" },
    { key: "corr", label: "Pearson Correlation", icon: "bi-graph-up-arrow" },
    { key: "anova", label: "ANOVA", icon: "bi-bar-chart-steps" },
  ];

  function StatBadge({ significant }) {
    return (
      <span
        style={{
          background: significant
            ? "rgba(25,135,84,0.1)"
            : "rgba(220,53,69,0.1)",
          color: significant ? "#198754" : "#dc3545",
          border: `1px solid ${significant ? "rgba(25,135,84,0.3)" : "rgba(220,53,69,0.3)"}`,
          padding: "3px 12px",
          borderRadius: 20,
          fontSize: 12,
          fontWeight: 500,
        }}
      >
        {significant
          ? "✓ Significant (p < 0.05)"
          : "✗ Not significant (p ≥ 0.05)"}
      </span>
    );
  }

  function MetricCards({ items }) {
    return (
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(4,minmax(0,1fr))",
          gap: 10,
          marginBottom: 14,
        }}
      >
        {items.map((m, i) => (
          <div
            key={i}
            style={{
              background: "#f8f9fa",
              borderRadius: 10,
              padding: "12px 14px",
              border: "1px solid #e9ecef",
            }}
          >
            <div style={{ fontSize: 10, color: "#aaa", marginBottom: 2 }}>
              {m.label}
            </div>
            <div
              style={{
                fontSize: 16,
                fontWeight: 700,
                color: m.color,
                textTransform: "capitalize",
              }}
            >
              {m.value}
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "flex", gap: 6, marginBottom: 18 }}>
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTest(t.key)}
            style={{
              padding: "7px 16px",
              borderRadius: 20,
              fontSize: 12.5,
              fontWeight: 500,
              border: `1px solid ${activeTest === t.key ? "#0d6efd" : "#e9ecef"}`,
              background:
                activeTest === t.key ? "rgba(13,110,253,0.08)" : "#f8f9fa",
              color: activeTest === t.key ? "#0d6efd" : "#666",
              cursor: "pointer",
            }}
          >
            <i className={`bi ${t.icon} me-1`} style={{ fontSize: 12 }}></i>
            {t.label}
          </button>
        ))}
      </div>

      {activeTest === "chi" &&
        (chiSquare?.chi2 ? (
          <div>
            <MetricCards
              items={[
                {
                  label: "χ² Statistic",
                  value: chiSquare.chi2,
                  color: "#0d6efd",
                },
                {
                  label: "p-value",
                  value: chiSquare.p_value,
                  color: chiSquare.significant ? "#198754" : "#dc3545",
                },
                {
                  label: "Degrees of freedom",
                  value: chiSquare.dof,
                  color: "#6f42c1",
                },
                {
                  label: "Result",
                  value: chiSquare.significant
                    ? "Significant"
                    : "Not significant",
                  color: chiSquare.significant ? "#198754" : "#dc3545",
                },
              ]}
            />
            <div style={{ marginBottom: 14 }}>
              <StatBadge significant={chiSquare.significant} />
            </div>
            <Interpretation icon="bi-grid-3x3" color="#0d6efd">
              <strong>Chi-square Interpretation:</strong>{" "}
              {chiInterp || "No interpretation available."}
            </Interpretation>
          </div>
        ) : (
          <div style={{ color: "#aaa", fontSize: 13, padding: "16px 0" }}>
            Chi-square test could not be computed — need at least 2 programs
            with 2+ students each.
          </div>
        ))}

      {activeTest === "corr" &&
        (correlation?.dow_vs_count ? (
          <div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 14,
                marginBottom: 14,
              }}
            >
              {[
                {
                  title: "Day of Week vs Visit Count",
                  data: correlation.dow_vs_count,
                  desc: "Tests if visit counts vary by day of week",
                },
                {
                  title: "Time Trend vs Visit Count",
                  data: correlation.trend_vs_count,
                  desc: "Tests if visits are growing or declining over time",
                },
              ].map((item, i) => (
                <div
                  key={i}
                  style={{
                    background: "#f8f9fa",
                    borderRadius: 10,
                    padding: "14px 16px",
                    border: "1px solid #e9ecef",
                  }}
                >
                  <div
                    style={{
                      fontSize: 13,
                      fontWeight: 600,
                      color: "#333",
                      marginBottom: 4,
                    }}
                  >
                    {item.title}
                  </div>
                  <div
                    style={{ fontSize: 11, color: "#aaa", marginBottom: 12 }}
                  >
                    {item.desc}
                  </div>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "1fr 1fr 1fr",
                      gap: 8,
                      marginBottom: 10,
                    }}
                  >
                    {[
                      { label: "r", value: item.data.r },
                      { label: "p-value", value: item.data.p },
                      { label: "Strength", value: item.data.strength },
                    ].map((m, j) => (
                      <div key={j}>
                        <div style={{ fontSize: 10, color: "#aaa" }}>
                          {m.label}
                        </div>
                        <div
                          style={{
                            fontSize: 15,
                            fontWeight: 600,
                            textTransform: "capitalize",
                            color:
                              m.label === "r"
                                ? item.data.r > 0
                                  ? "#198754"
                                  : "#dc3545"
                                : "#333",
                          }}
                        >
                          {m.value}
                        </div>
                      </div>
                    ))}
                  </div>
                  <StatBadge significant={item.data.significant} />
                </div>
              ))}
            </div>
            <Interpretation icon="bi-graph-up-arrow" color="#198754">
              <strong>Pearson Correlation Interpretation:</strong>{" "}
              {corrInterp || "No interpretation available."}
            </Interpretation>
          </div>
        ) : (
          <div style={{ color: "#aaa", fontSize: 13, padding: "16px 0" }}>
            Pearson correlation could not be computed — not enough data points.
          </div>
        ))}

      {activeTest === "anova" &&
        (anova?.f_stat ? (
          <div>
            <MetricCards
              items={[
                { label: "F-statistic", value: anova.f_stat, color: "#0d6efd" },
                {
                  label: "p-value",
                  value: anova.p_value,
                  color: anova.significant ? "#198754" : "#dc3545",
                },
                { label: "Groups", value: anova.n_groups, color: "#6f42c1" },
                {
                  label: "Result",
                  value: anova.significant ? "Significant" : "Not significant",
                  color: anova.significant ? "#198754" : "#dc3545",
                },
              ]}
            />
            <div style={{ marginBottom: 14 }}>
              <StatBadge significant={anova.significant} />
            </div>
            <div style={{ overflowX: "auto", marginBottom: 14 }}>
              <table
                style={{
                  width: "100%",
                  borderCollapse: "collapse",
                  fontSize: 12.5,
                }}
              >
                <thead>
                  <tr style={{ background: "#f8f9fa" }}>
                    {["Program", "n", "Mean visits", "Std dev"].map((h, i) => (
                      <th
                        key={i}
                        style={{
                          padding: "8px 12px",
                          textAlign: "left",
                          fontWeight: 600,
                          color: "#555",
                          border: "1px solid #e9ecef",
                        }}
                      >
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {anova.group_means?.map((g, i) => (
                    <tr
                      key={i}
                      style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}
                    >
                      <td
                        style={{
                          padding: "7px 12px",
                          fontWeight: 500,
                          border: "1px solid #f0f0f0",
                        }}
                      >
                        {g.program}
                      </td>
                      <td
                        style={{
                          padding: "7px 12px",
                          color: "#888",
                          border: "1px solid #f0f0f0",
                        }}
                      >
                        {g.n}
                      </td>
                      <td
                        style={{
                          padding: "7px 12px",
                          border: "1px solid #f0f0f0",
                        }}
                      >
                        <span
                          style={{
                            background: "rgba(13,110,253,0.08)",
                            color: "#0d6efd",
                            padding: "2px 8px",
                            borderRadius: 20,
                            fontWeight: 700,
                            fontSize: 12,
                          }}
                        >
                          {g.mean_visits}
                        </span>
                      </td>
                      <td
                        style={{
                          padding: "7px 12px",
                          color: "#888",
                          border: "1px solid #f0f0f0",
                        }}
                      >
                        ±{g.std_visits}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Interpretation icon="bi-bar-chart-steps" color="#6f42c1">
              <strong>ANOVA Interpretation:</strong>{" "}
              {anovaInterp || "No interpretation available."}
            </Interpretation>
          </div>
        ) : (
          <div style={{ color: "#aaa", fontSize: 13, padding: "16px 0" }}>
            ANOVA could not be computed — need at least 2 programs with 2+
            students each.
          </div>
        ))}
    </div>
  );
}

// ── Segmentation Section ──────────────────────────────────────
function SegmentationSection({ seg }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const [tab, setTab] = React.useState("regular");

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !seg?.segment_counts) return;
    if (chartRef.current) chartRef.current.destroy();
    chartRef.current = new window.Chart(canvasRef.current, {
      type: "doughnut",
      data: {
        labels: seg.segment_labels,
        datasets: [
          {
            data: seg.segment_counts,
            backgroundColor: seg.segment_colors,
            borderWidth: 3,
            borderColor: "#fff",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "68%",
        plugins: {
          legend: {
            position: "bottom",
            labels: { font: { size: 11 }, padding: 14, boxWidth: 12 },
          },
          tooltip: { callbacks: { label: (ctx) => ` ${ctx.parsed} students` } },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [seg]);

  if (!seg) return null;
  const total = seg.regular_count + seg.occasional_count + seg.rare_count;
  const tabs = {
    regular: {
      list: seg.regular,
      color: "#198754",
      label: "Regular",
      count: seg.regular_count,
    },
    occasional: {
      list: seg.occasional,
      color: "#ffc107",
      label: "Occasional",
      count: seg.occasional_count,
    },
    rare: {
      list: seg.rare,
      color: "#dc3545",
      label: "Rare",
      count: seg.rare_count,
    },
  };
  const regularPct =
    total > 0 ? Math.round((seg.regular_count / total) * 100) : 0;
  const occasionalPct =
    total > 0 ? Math.round((seg.occasional_count / total) * 100) : 0;
  const rarePct = total > 0 ? Math.round((seg.rare_count / total) * 100) : 0;

  return (
    <div>
      <div style={{ fontSize: 12, color: "#888", marginBottom: 16 }}>
        Segmented by average weekly visits over{" "}
        <strong>{seg.total_weeks} weeks</strong> of cleaned data.
      </div>
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 2fr",
          gap: 20,
          marginBottom: 4,
        }}
      >
        <div style={{ height: 200, position: "relative" }}>
          <canvas ref={canvasRef}></canvas>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {Object.entries(tabs).map(([key, s]) => (
            <div
              key={key}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "10px 14px",
                background: "#f8f9fa",
                borderRadius: 8,
                border: `1px solid ${s.color}30`,
              }}
            >
              <div
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  background: s.color,
                  flexShrink: 0,
                }}
              ></div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: "#333" }}>
                  {s.label}
                </div>
                <div style={{ fontSize: 11, color: "#aaa" }}>
                  {key === "regular"
                    ? "3+ visits/week"
                    : key === "occasional"
                      ? "1–2 visits/week"
                      : "<1 visit/week"}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div style={{ fontSize: 18, fontWeight: 800, color: s.color }}>
                  {s.count}
                </div>
                <div style={{ fontSize: 10.5, color: "#bbb" }}>
                  {total > 0 ? Math.round((s.count / total) * 100) : 0}%
                </div>
              </div>
              <div style={{ width: 60 }}>
                <div
                  style={{ height: 5, background: "#e9ecef", borderRadius: 99 }}
                >
                  <div
                    style={{
                      height: "100%",
                      borderRadius: 99,
                      background: s.color,
                      width: `${total > 0 ? (s.count / total) * 100 : 0}%`,
                    }}
                  ></div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <Interpretation icon="bi-people" color="#198754">
        <strong>Segmentation Interpretation:</strong> Out of{" "}
        <strong>{total}</strong> total students analyzed,{" "}
        <strong style={{ color: "#198754" }}>
          {seg.regular_count} ({regularPct}%)
        </strong>{" "}
        are regular library users (3+/week),{" "}
        <strong style={{ color: "#ffc107" }}>
          {seg.occasional_count} ({occasionalPct}%)
        </strong>{" "}
        visit occasionally (1–2/week), and{" "}
        <strong style={{ color: "#dc3545" }}>
          {seg.rare_count} ({rarePct}%)
        </strong>{" "}
        rarely visit (&lt;once/week).{" "}
        {regularPct >= 30
          ? "The library enjoys a strong base of regular users."
          : rarePct >= 50
            ? "A majority of students are rare visitors — outreach programs may help increase usage."
            : "The student population shows a balanced distribution across segments."}
      </Interpretation>
      <div style={{ marginTop: 20 }}>
        <div style={{ display: "flex", gap: 6, marginBottom: 12 }}>
          {Object.entries(tabs).map(([key, s]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              style={{
                padding: "6px 14px",
                borderRadius: 20,
                fontSize: 12,
                fontWeight: 500,
                border: `1px solid ${tab === key ? s.color : s.color + "40"}`,
                background: tab === key ? s.color + "15" : "transparent",
                color: tab === key ? s.color : "#888",
                cursor: "pointer",
                transition: "all 0.15s",
              }}
            >
              <span style={{ fontWeight: 700 }}>{s.count}</span> {s.label}
            </button>
          ))}
        </div>
        <div style={{ overflowX: "auto" }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 12.5,
            }}
          >
            <thead>
              <tr style={{ background: "#f8f9fa" }}>
                {[
                  "#",
                  "Name",
                  "SR Code",
                  "Program",
                  "Year",
                  "Total Visits",
                  "Weekly Avg",
                ].map((h, i) => (
                  <th
                    key={i}
                    style={{
                      padding: "9px 12px",
                      textAlign: "left",
                      fontWeight: 600,
                      color: "#555",
                      border: "1px solid #e9ecef",
                      whiteSpace: "nowrap",
                    }}
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {tabs[tab].list.length === 0 ? (
                <tr>
                  <td
                    colSpan={7}
                    style={{
                      padding: "24px",
                      textAlign: "center",
                      color: "#bbb",
                    }}
                  >
                    No students in this segment.
                  </td>
                </tr>
              ) : (
                tabs[tab].list.map((s, i) => (
                  <tr
                    key={i}
                    style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}
                  >
                    <td
                      style={{
                        padding: "8px 12px",
                        color: "#bbb",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {i + 1}
                    </td>
                    <td
                      style={{
                        padding: "8px 12px",
                        fontWeight: 500,
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {s.name}
                    </td>
                    <td
                      style={{
                        padding: "8px 12px",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      <code style={{ fontSize: 11 }}>{s.sr_code}</code>
                    </td>
                    <td
                      style={{
                        padding: "8px 12px",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {s.program}
                    </td>
                    <td
                      style={{
                        padding: "8px 12px",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {s.year_level || "—"}
                    </td>
                    <td
                      style={{
                        padding: "8px 12px",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      <span
                        style={{
                          background: tabs[tab].color + "20",
                          color: tabs[tab].color,
                          padding: "2px 8px",
                          borderRadius: 20,
                          fontWeight: 700,
                          fontSize: 12,
                        }}
                      >
                        {s.total_visits}
                      </span>
                    </td>
                    <td
                      style={{
                        padding: "8px 12px",
                        color: "#888",
                        border: "1px solid #f0f0f0",
                      }}
                    >
                      {s.weekly_avg}×/wk
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
        {tabs[tab].list.length > 0 && (
          <Interpretation icon="bi-table" color="#6c757d">
            <strong>{tabs[tab].label} Students Table:</strong>{" "}
            {tab === "regular"
              ? `These ${seg.regular_count} students consistently use the library 3+ times/week. Top visitor: ${tabs[tab].list[0]?.name} with ${tabs[tab].list[0]?.total_visits} total visits.`
              : tab === "occasional"
                ? `These ${seg.occasional_count} students visit 1–2 times/week. Targeted engagement could convert some into regular visitors.`
                : `These ${seg.rare_count} students visit less than once per week. Understanding barriers to library usage could help increase engagement.`}
          </Interpretation>
        )}
      </div>
    </div>
  );
}

// ── Anomaly Section ───────────────────────────────────────────
function AnomalySection({ anomalies, mean, stdDev }) {
  if (!anomalies) return null;
  const spikes = anomalies.filter((a) => a.type === "spike");
  const drops = anomalies.filter((a) => a.type === "drop");

  return (
    <div>
      <div style={{ fontSize: 12, color: "#888", marginBottom: 14 }}>
        Days deviating <strong>±2 standard deviations</strong> from the mean (
        {mean} ± {stdDev} visits/day) are flagged using the Z-score method.
      </div>
      {anomalies.length === 0 ? (
        <div
          style={{
            background: "rgba(25,135,84,0.07)",
            border: "1px solid rgba(25,135,84,0.2)",
            borderRadius: 8,
            padding: "12px 16px",
            fontSize: 13,
            color: "#198754",
            marginBottom: 4,
          }}
        >
          <i className="bi bi-check-circle-fill me-2"></i>No anomalies detected
          — visit patterns are consistent across the dataset.
        </div>
      ) : (
        <div style={{ overflowX: "auto", marginBottom: 4 }}>
          <table
            style={{
              width: "100%",
              borderCollapse: "collapse",
              fontSize: 12.5,
            }}
          >
            <thead>
              <tr style={{ background: "#f8f9fa" }}>
                {["Date", "Day", "Visits", "Type", "Z-Score", "Deviation"].map(
                  (h, i) => (
                    <th
                      key={i}
                      style={{
                        padding: "9px 12px",
                        textAlign: "left",
                        fontWeight: 600,
                        color: "#555",
                        border: "1px solid #e9ecef",
                      }}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {anomalies.map((a, i) => (
                <tr
                  key={i}
                  style={{ background: i % 2 === 0 ? "#fff" : "#fafafa" }}
                >
                  <td
                    style={{
                      padding: "8px 12px",
                      fontWeight: 500,
                      border: "1px solid #f0f0f0",
                    }}
                  >
                    {a.date}
                  </td>
                  <td
                    style={{
                      padding: "8px 12px",
                      color: "#888",
                      border: "1px solid #f0f0f0",
                    }}
                  >
                    {a.day}
                  </td>
                  <td
                    style={{ padding: "8px 12px", border: "1px solid #f0f0f0" }}
                  >
                    <span
                      style={{
                        background:
                          a.type === "spike"
                            ? "rgba(220,53,69,0.12)"
                            : "rgba(108,117,125,0.12)",
                        color: a.type === "spike" ? "#dc3545" : "#6c757d",
                        padding: "2px 8px",
                        borderRadius: 20,
                        fontWeight: 700,
                        fontSize: 12,
                      }}
                    >
                      {a.count}
                    </span>
                  </td>
                  <td
                    style={{ padding: "8px 12px", border: "1px solid #f0f0f0" }}
                  >
                    <span
                      style={{
                        fontSize: 12,
                        fontWeight: 600,
                        color: a.type === "spike" ? "#dc3545" : "#6c757d",
                      }}
                    >
                      {a.type === "spike" ? "↑ Spike" : "↓ Drop"}
                    </span>
                  </td>
                  <td
                    style={{ padding: "8px 12px", border: "1px solid #f0f0f0" }}
                  >
                    <code style={{ fontSize: 11 }}>{a.z_score}</code>
                  </td>
                  <td
                    style={{
                      padding: "8px 12px",
                      color: "#888",
                      fontSize: 12,
                      border: "1px solid #f0f0f0",
                    }}
                  >
                    {a.deviation}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <Interpretation icon="bi-exclamation-triangle" color="#dc3545">
        <strong>Anomaly Detection Interpretation:</strong>{" "}
        {anomalies.length === 0 ? (
          "No statistically significant anomalies were found. Daily visit counts remain within normal bounds (±2 std dev). Lower Saturday/Sunday activity is expected — those are not regular library-open days."
        ) : (
          <>
            The Z-score analysis identified <strong>{anomalies.length}</strong>{" "}
            anomalous day{anomalies.length !== 1 ? "s" : ""} —{" "}
            <strong style={{ color: "#dc3545" }}>
              {spikes.length} spike{spikes.length !== 1 ? "s" : ""}
            </strong>{" "}
            and{" "}
            <strong style={{ color: "#6c757d" }}>
              {drops.length} drop{drops.length !== 1 ? "s" : ""}
            </strong>
            .{" "}
            {spikes.length > 0 && (
              <>
                Spikes may correspond to exam periods, special events, or
                deadlines.{" "}
              </>
            )}
            {drops.length > 0 && (
              <>
                Drops may indicate holidays, system downtime, or campus-wide
                events.{" "}
              </>
            )}
            Cross-reference with the academic calendar to validate findings.
          </>
        )}
      </Interpretation>
    </div>
  );
}

const ANALYTICS_COLORS = {
  primary: "#2563eb",
  secondary: "#0f766e",
  accent: "#f97316",
  rose: "#e11d48",
  gold: "#d97706",
  ink: "#0f172a",
  muted: "#64748b",
  panel: "#ffffff",
  border: "rgba(148, 163, 184, 0.22)",
  grid: "rgba(148, 163, 184, 0.18)",
};

function formatHourLabel(hour) {
  const normalized = Number(hour) || 0;
  const suffix = normalized >= 12 ? "PM" : "AM";
  const displayHour = normalized % 12 || 12;
  return `${displayHour} ${suffix}`;
}

function getTrendDirection(counts = []) {
  if (!counts.length) return "stable";
  const firstWindow = counts.slice(0, Math.min(7, counts.length));
  const lastWindow = counts.slice(-Math.min(7, counts.length));
  const firstAvg = firstWindow.reduce((sum, value) => sum + value, 0) / Math.max(firstWindow.length, 1);
  const lastAvg = lastWindow.reduce((sum, value) => sum + value, 0) / Math.max(lastWindow.length, 1);
  if (lastAvg > firstAvg + 1) return "upward";
  if (lastAvg < firstAvg - 1) return "downward";
  return "stable";
}

function getPeakDow(dowLabels = [], dowAverages = []) {
  if (!dowAverages.length) return { label: "—", value: 0 };
  const peakValue = Math.max(...dowAverages);
  const peakIndex = dowAverages.indexOf(peakValue);
  return {
    label: dowLabels[peakIndex] || "—",
    value: peakValue || 0,
  };
}

function getPeakHour(peakHours = []) {
  if (!peakHours.length) return { label: "—", value: 0 };
  const peak = [...peakHours].sort((a, b) => (b.count || 0) - (a.count || 0))[0];
  return {
    label: formatHourLabel(peak?.hour),
    value: peak?.count || 0,
  };
}

function getTopProgram(programDistribution = []) {
  if (!programDistribution.length) return { label: "No dominant program yet", value: 0 };
  const top = programDistribution[0];
  return {
    label: top?.program || "Unknown",
    value: top?.count || 0,
  };
}

function getYearLevelLabel(item) {
  return item?.year_level || item?.label || "Unknown";
}

function normalizeYearLevelLabel(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "Unknown";

  const lowered = normalized.toLowerCase().replace(/-/g, " ");
  const aliases = {
    "1": "1st Year",
    "1st": "1st Year",
    "1st year": "1st Year",
    "first year": "1st Year",
    "2": "2nd Year",
    "2nd": "2nd Year",
    "2nd year": "2nd Year",
    "second year": "2nd Year",
    "3": "3rd Year",
    "3rd": "3rd Year",
    "3rd year": "3rd Year",
    "third year": "3rd Year",
    "4": "4th Year",
    "4th": "4th Year",
    "4th year": "4th Year",
    "fourth year": "4th Year",
    "5": "5th Year",
    "5th": "5th Year",
    "5th year": "5th Year",
    "fifth year": "5th Year",
    "6": "6th Year",
    "6th": "6th Year",
    "6th year": "6th Year",
    "sixth year": "6th Year",
    unknown: "Unknown",
  };

  return aliases[lowered] || normalized;
}

function getYearLevelOrder(label) {
  const normalized = normalizeYearLevelLabel(label);
  const lookup = {
    "1st Year": 1,
    "2nd Year": 2,
    "3rd Year": 3,
    "4th Year": 4,
    "5th Year": 5,
    "6th Year": 6,
    Unknown: 99,
  };
  return lookup[normalized] ?? 98;
}

function normalizeYearLevelData(items = []) {
  const merged = new Map();

  items.forEach((item) => {
    const label = normalizeYearLevelLabel(getYearLevelLabel(item));
    const count = Number(item?.count || 0);
    merged.set(label, (merged.get(label) || 0) + count);
  });

  return [...merged.entries()].map(([label, count]) => ({
    year_level: label,
    count,
  })).filter(item => item.count > 0).sort((a, b) => {
    const labelA = a.year_level;
    const labelB = b.year_level;
    const orderDelta = getYearLevelOrder(labelA) - getYearLevelOrder(labelB);
    if (orderDelta !== 0) return orderDelta;
    return labelA.localeCompare(labelB);
  });
}

function buildInsightCards(currentData) {
  if (!currentData?.descriptive_stats) return [];
  
  const stats = currentData?.descriptive_stats || {};
  const peakDow = getPeakDow(currentData?.dow_labels, currentData?.dow_averages);
  const peakHour = getPeakHour(currentData?.peak_hours);
  const topProgram = getTopProgram(currentData?.program_distribution);
  const trend = getTrendDirection(currentData?.last_30_counts);

  return [
    {
      icon: "bi bi-calendar-week",
      color: ANALYTICS_COLORS.primary,
      title: "Busiest weekday",
      body: `${peakDow.label} leads with ${peakDow.value} average visits.`,
    },
    {
      icon: "bi bi-clock-history",
      color: ANALYTICS_COLORS.secondary,
      title: "Peak entry hour",
      body: `${peakHour.label} shows the strongest traffic at ${peakHour.value} visits.`,
    },
    {
      icon: "bi bi-mortarboard",
      color: ANALYTICS_COLORS.accent,
      title: "Most represented program",
      body: `${topProgram.label} contributes ${topProgram.value} recorded visits.`,
    },
    {
      icon: "bi bi-activity",
      color: ANALYTICS_COLORS.rose,
      title: "30-day pattern",
      body:
        trend === "upward"
          ? "Recent activity is trending upward versus the start of the month."
          : trend === "downward"
            ? "Recent activity has cooled compared with the start of the month."
            : `Daily usage is relatively stable around ${stats?.mean_daily_visits || 0} visits per day.`,
    },
  ];
}

function hexToRgba(hex, alpha) {
  if (typeof hex !== "string" || !hex.startsWith("#")) {
    return `rgba(37, 99, 235, ${alpha})`;
  }

  const normalized = hex.length === 4
    ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}`
    : hex;
  const red = parseInt(normalized.slice(1, 3), 16);
  const green = parseInt(normalized.slice(3, 5), 16);
  const blue = parseInt(normalized.slice(5, 7), 16);

  if ([red, green, blue].some(Number.isNaN)) {
    return `rgba(37, 99, 235, ${alpha})`;
  }

  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function buildChartJsConfig({ options, series }) {
  const type = options?.chart?.type || "line";
  const normalizedType = type === "area" ? "line" : type;
  const colors = options?.colors?.length
    ? options.colors
    : [ANALYTICS_COLORS.primary, ANALYTICS_COLORS.secondary, ANALYTICS_COLORS.accent];
  const labels = options?.labels || options?.xaxis?.categories || [];
  const normalizedSeries = Array.isArray(series) ? series : [];
  const isPieLike = normalizedType === "donut" || normalizedType === "pie";
  const isRadar = normalizedType === "radar";
  const isHorizontalBar =
    normalizedType === "bar" && Boolean(options?.plotOptions?.bar?.horizontal);
  const chartType = isPieLike ? "doughnut" : normalizedType;
  const isLineLike = chartType === "line" || isRadar;
  const tooltipYFormatter =
    typeof options?.tooltip?.y?.formatter === "function"
      ? options.tooltip.y.formatter
      : null;

  function getTooltipValue(ctx) {
    if (isPieLike) return ctx?.parsed;
    if (isRadar) return ctx?.parsed?.r;
    if (isHorizontalBar) return ctx?.parsed?.x;
    return ctx?.parsed?.y;
  }

  const datasets = isPieLike
    ? [
        {
          label: "Values",
          data: normalizedSeries.map((value) => Number(value) || 0),
          backgroundColor: labels.map((_, index) => colors[index % colors.length]),
          borderColor: "#ffffff",
          borderWidth: 2,
          hoverOffset: 6,
        },
      ]
    : normalizedSeries.map((entry, index) => {
        const color = colors[index % colors.length];
        const rawData = Array.isArray(entry?.data) ? entry.data : [];
        const pointRadius = typeof options?.markers?.size === "number" ? options.markers.size : 0;
        const isFilled = type === "area" || Boolean(options?.fill) || isRadar;
        const strokeWidth = typeof options?.stroke?.width === "number" ? options.stroke.width : 3;

        return {
          label: entry?.name || `Series ${index + 1}`,
          data: rawData,
          borderColor: color,
          backgroundColor:
            chartType === "bar"
              ? rawData.map((_, itemIndex) => colors[itemIndex % colors.length] || color)
              : hexToRgba(color, isRadar ? 0.24 : 0.18),
          borderWidth: strokeWidth,
          fill: chartType === "line" && isFilled,
          tension: chartType === "line" ? 0.38 : 0,
          pointRadius: isRadar ? Math.max(pointRadius, 5) : pointRadius,
          pointHoverRadius: isRadar
            ? Math.max(pointRadius + 5, 10)
            : pointRadius > 0
              ? pointRadius + 2
              : 5,
          pointHitRadius: isRadar ? 28 : isLineLike ? 18 : pointRadius + 4,
          borderRadius: typeof options?.plotOptions?.bar?.borderRadius === "number"
            ? options.plotOptions.bar.borderRadius
            : undefined,
        };
      });

  const config = {
    type: chartType,
    data: {
      labels,
      datasets,
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: isHorizontalBar ? "y" : "x",
      interaction: isRadar
        ? {
            mode: "nearest",
            intersect: false,
          }
        : isLineLike
          ? {
              mode: "index",
              intersect: false,
            }
          : undefined,
      plugins: {
        legend: {
          display: options?.legend?.show !== false,
          position: options?.legend?.position || "top",
          labels: {
            color: ANALYTICS_COLORS.muted,
            font: { size: 11 },
            padding: 14,
            boxWidth: 12,
          },
        },
        tooltip: {
          backgroundColor: "rgba(15, 23, 42, 0.94)",
          titleColor: "#f8fafc",
          bodyColor: "#e2e8f0",
          borderColor: "rgba(148, 163, 184, 0.18)",
          borderWidth: 1,
          callbacks: isPieLike
            ? undefined
            : {
                title: (items) => {
                  const item = items?.[0];
                  return item?.label || "";
                },
                label: (ctx) => {
                  if (tooltipYFormatter) {
                    return ` ${tooltipYFormatter(getTooltipValue(ctx) ?? 0, {
                      dataPointIndex: ctx.dataIndex,
                      seriesIndex: ctx.datasetIndex,
                    })}`;
                  }
                  return ` ${getTooltipValue(ctx) ?? 0} visits`;
                },
              },
        },
      },
    },
  };

  if (isPieLike) {
    config.options.cutout = options?.plotOptions?.pie?.donut?.size || "68%";
  } else if (isRadar) {
    config.options.layout = {
      padding: {
        top: 14,
        right: 18,
        bottom: 14,
        left: 18,
      },
    };
    config.options.scales = {
      r: {
        angleLines: { color: ANALYTICS_COLORS.grid },
        grid: { color: ANALYTICS_COLORS.grid },
        pointLabels: {
          color: ANALYTICS_COLORS.muted,
          font: { size: 11 },
        },
        ticks: {
          display: false,
          backdropColor: "transparent",
        },
      },
    };
  } else {
    config.options.scales = {
      x: {
        grid: { display: false },
        ticks: {
          color: ANALYTICS_COLORS.muted,
          font: { size: 11 },
          maxRotation: isHorizontalBar ? 0 : 40,
        },
      },
      y: {
        beginAtZero: true,
        grid: { color: ANALYTICS_COLORS.grid },
        ticks: {
          color: ANALYTICS_COLORS.muted,
          font: { size: 11 },
        },
        title: options?.yaxis?.title?.text
          ? {
              display: true,
              text: options.yaxis.title.text,
              color: ANALYTICS_COLORS.muted,
              font: { size: 11, weight: "600" },
            }
          : undefined,
      },
    };
  }

  return config;
}

function ApexChartPanel({ title, subtitle, height = 320, options, series }) {
  const chartRef = React.useRef(null);
  const canvasRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !series?.length) return;
    if (chartRef.current) {
      chartRef.current.destroy();
      chartRef.current = null;
    }

    try {
      chartRef.current = new window.Chart(
        canvasRef.current,
        buildChartJsConfig({ options, series }),
      );
    } catch (error) {
      console.error(`Failed to render chart "${title}"`, error);
      chartRef.current?.destroy();
      chartRef.current = null;
    }

    return () => {
      chartRef.current?.destroy();
      chartRef.current = null;
    };
  }, [options, series, title]);

  if (!series?.length) return null;

  return (
    <div
      style={{
        background: ANALYTICS_COLORS.panel,
        borderRadius: 24,
        border: `1px solid ${ANALYTICS_COLORS.border}`,
        padding: 22,
        boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)",
      }}
    >
      <div style={{ marginBottom: 14 }}>
        <div
          style={{
            fontSize: 17,
            fontWeight: 800,
            color: ANALYTICS_COLORS.ink,
            lineHeight: 1.2,
          }}
        >
          {title}
        </div>
        {subtitle ? (
          <div
            style={{
              fontSize: 12.5,
              color: ANALYTICS_COLORS.muted,
              marginTop: 6,
            }}
          >
            {subtitle}
          </div>
        ) : null}
      </div>
      <div style={{ height, position: "relative" }}>
        <canvas ref={canvasRef}></canvas>
      </div>
    </div>
  );
}

function InsightChip({ icon, color, title, body }) {
  return (
    <div
      style={{
        background: "#fff",
        border: `1px solid ${ANALYTICS_COLORS.border}`,
        borderRadius: 20,
        padding: "16px 18px",
        display: "flex",
        gap: 14,
        alignItems: "flex-start",
        boxShadow: "0 12px 30px rgba(15, 23, 42, 0.06)",
      }}
    >
      <div
        style={{
          width: 42,
          height: 42,
          borderRadius: 14,
          background: `${color}18`,
          color,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          flexShrink: 0,
          fontSize: 18,
        }}
      >
        <i className={icon}></i>
      </div>
      <div>
        <div
          style={{
            fontSize: 13,
            fontWeight: 800,
            color: ANALYTICS_COLORS.ink,
            marginBottom: 4,
          }}
        >
          {title}
        </div>
        <div style={{ fontSize: 12.5, color: ANALYTICS_COLORS.muted, lineHeight: 1.65 }}>
          {body}
        </div>
      </div>
    </div>
  );
}

function SummaryStat({ label, value, tone, icon, helper }) {
  return (
    <div
      style={{
        background: "rgba(255,255,255,0.12)",
        border: "1px solid rgba(255,255,255,0.16)",
        borderRadius: 22,
        padding: "18px 18px 16px",
        backdropFilter: "blur(10px)",
        minHeight: 120,
      }}
    >
      <div
        style={{
          width: 40,
          height: 40,
          borderRadius: 14,
          background: `${tone}22`,
          color: "#fff",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: 18,
          fontSize: 17,
        }}
      >
        <i className={icon}></i>
      </div>
      <div style={{ color: "rgba(255,255,255,0.72)", fontSize: 12, letterSpacing: "0.04em", textTransform: "uppercase", fontWeight: 700 }}>
        {label}
      </div>
      <div style={{ color: "#fff", fontWeight: 800, fontSize: 28, lineHeight: 1.1, marginTop: 8 }}>
        {value}
      </div>
      <div style={{ color: "rgba(255,255,255,0.72)", fontSize: 12.5, marginTop: 6 }}>
        {helper}
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────
function AnalyticsReportsInner() {
  const { session } = useSession();
  const initialCacheRef = React.useRef(undefined);
  if (initialCacheRef.current === undefined) {
    initialCacheRef.current = readAnalyticsCache();
  }
  const initialCache = initialCacheRef.current;
  const [basicData, setBasicData] = React.useState(initialCache?.data ?? null);
  const [error, setError] = React.useState(false);
  const [errorMessage, setErrorMessage] = React.useState("");
  const [loading, setLoading] = React.useState(!initialCache?.data);
  const [refreshing, setRefreshing] = React.useState(false);
  const [socketConnected, setSocketConnected] = React.useState(socket.connected);
  const [lastUpdatedAt, setLastUpdatedAt] = React.useState(
    initialCache?.timestamp ? new Date(initialCache.timestamp) : null,
  );
  const headerRef = React.useRef(null);
  const refreshInFlightRef = React.useRef(false);
  const hasLoadedDataRef = React.useRef(false);
  const lastRefreshAtRef = React.useRef(initialCache?.timestamp ?? 0);
  const lastPayloadRef = React.useRef(initialCache?.serialized ?? "");

  React.useEffect(() => {
    hasLoadedDataRef.current = Boolean(basicData);
  }, [basicData]);

  async function runAnalyticsPipeline({ silent = false, force = false } = {}) {
    const now = Date.now();
    if (refreshInFlightRef.current) return;
    if (!force && now - lastRefreshAtRef.current < ANALYTICS_MIN_REFRESH_INTERVAL_MS) {
      return;
    }

    refreshInFlightRef.current = true;
    lastRefreshAtRef.current = now;

    if (!silent && !hasLoadedDataRef.current) {
      setLoading(true);
    } else if (!silent) {
      setRefreshing(true);
    }
    setError(false);
    setErrorMessage("");

    try {
      const basic = await fetchJson("/api/analytics-basic");
      const refreshedAt = Date.now();
      const serialized = JSON.stringify(basic);

      if (serialized !== lastPayloadRef.current) {
        lastPayloadRef.current = serialized;
        setBasicData(basic);
      }

      writeAnalyticsCache(basic, refreshedAt);
      setLastUpdatedAt(new Date(refreshedAt));
      setLoading(false);
    } catch (err) {
      console.error("Analytics pipeline error:", err);
      setErrorMessage(
        getErrorMessage(
          err,
          "Failed to load analytics data. Check the analytics API or try syncing again."
        )
      );
      if (!hasLoadedDataRef.current) {
        setError(true);
      }
      setLoading(false);
    } finally {
      setRefreshing(false);
      refreshInFlightRef.current = false;
    }
  }

  React.useEffect(() => {
    if (!initialCache?.isFresh) {
      runAnalyticsPipeline({
        silent: Boolean(initialCache?.data),
        force: true,
      });
    }

    // Handle tab visibility changes for real-time updates
    const handleVisibilityChange = () => {
      if (!document.hidden) {
        // Tab became visible again - refresh silently (no spinner)
        runAnalyticsPipeline({ silent: true });
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, []);

  React.useEffect(() => {

    const intervalMs = socketConnected
      ? ANALYTICS_CONNECTED_POLL_MS
      : ANALYTICS_FALLBACK_POLL_MS;
    const timer = window.setInterval(() => {

      runAnalyticsPipeline({ silent: true });
    }, intervalMs);

    return () => {
      window.clearInterval(timer);
    };
  }, [socketConnected]);

  React.useEffect(() => {
    function handleAnalyticsUpdated() {
      runAnalyticsPipeline({ silent: true, force: true });
    }

    function handleConnect() {
      setSocketConnected(true);
    }

    function handleDisconnect() {
      setSocketConnected(false);
    }

    socket.connect();
    socket.on("connect", handleConnect);
    socket.on("disconnect", handleDisconnect);
    socket.on("analytics_updated", handleAnalyticsUpdated);
    return () => {
      socket.off("connect", handleConnect);
      socket.off("disconnect", handleDisconnect);
      socket.off("analytics_updated", handleAnalyticsUpdated);
      socket.disconnect();
    };
  }, []);

  React.useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([e]) => el.classList.toggle("shadow-sm", !e.isIntersecting),
      { threshold: 1 },
    );
    const sentinel = document.createElement("div");
    el.parentNode.insertBefore(sentinel, el);
    obs.observe(sentinel);
    return () => obs.disconnect();
  }, []);

  const currentData = basicData;
  const dq = currentData?.data_quality;
  const totalRemoved =
    typeof dq?.total_removed === "number"
      ? dq.total_removed
      : (
          Number(dq?.removed_low_conf || 0)
          + Number(dq?.removed_outside_hrs || 0)
          + Number(dq?.removed_duplicates || 0)
        );
  const stats = currentData?.descriptive_stats;
  const dowLabels = currentData?.dow_labels;
  const dowAverages = currentData?.dow_averages;
  const last30Labels = currentData?.last_30_labels;
  const last30Counts = currentData?.last_30_counts;
  // Check if API returned an error response (has message but no data_quality)
  const noAnalyticsData = Boolean(currentData?.message && !currentData?.data_quality);
  const initialLoadFailed = !loading && !basicData && error;
  // hasRenderableData means we have valid analytics data (not just error object)
  const hasRenderableData = Boolean(currentData?.data_quality);
  const canManageImports =
    session?.role === "super_admin" || session?.role === "library_admin";
  const lastUpdatedLabel = lastUpdatedAt
    ? lastUpdatedAt.toLocaleTimeString([], {
        hour: "numeric",
        minute: "2-digit",
        second: "2-digit",
      })
    : "Waiting for first sync";
  const peakHours = currentData?.peak_hours || [];
  const programDistribution = currentData?.program_distribution || [];
  const genderData = currentData?.gender_data || [];
  const yearLevelData = normalizeYearLevelData(currentData?.year_level_data || []);
  const trendDirection = getTrendDirection(last30Counts);
  const peakDow = getPeakDow(dowLabels, dowAverages);
  const peakHour = getPeakHour(peakHours);
  const topProgram = getTopProgram(programDistribution);
  const insightCards = buildInsightCards(currentData);
  const last30Total = (last30Counts || []).reduce((sum, value) => sum + value, 0);
  const weekdayAverage =
    dowAverages?.length
      ? (dowAverages.reduce((sum, value) => sum + value, 0) / dowAverages.length).toFixed(1)
      : "0.0";

  if (hasRenderableData && !noAnalyticsData) {
    const heroGradient =
      trendDirection === "upward"
        ? "linear-gradient(135deg, #0f172a 0%, #1d4ed8 45%, #0f766e 100%)"
        : trendDirection === "downward"
          ? "linear-gradient(135deg, #0f172a 0%, #7c2d12 45%, #be123c 100%)"
          : "linear-gradient(135deg, #0f172a 0%, #1e293b 45%, #2563eb 100%)";

    const visitsTrendSeries = [
      {
        name: "Daily visits",
        data: last30Counts || [],
      },
    ];

    const visitsTrendOptions = {
      chart: { type: "area" },
      colors: [ANALYTICS_COLORS.primary],
      fill: {
        type: "gradient",
        gradient: {
          shadeIntensity: 1,
          opacityFrom: 0.36,
          opacityTo: 0.04,
          stops: [0, 95, 100],
        },
      },
      markers: { size: 0 },
      xaxis: {
        categories: last30Labels || [],
      },
      yaxis: {
        title: { text: "Visits" },
      },
    };

    const dowSeries = [
      {
        name: "Average visits",
        data: dowAverages || [],
      },
    ];

    const dowOptions = {
      chart: { type: "bar" },
      colors: [ANALYTICS_COLORS.secondary],
      plotOptions: {
        bar: {
          borderRadius: 10,
          columnWidth: "52%",
        },
      },
      xaxis: {
        categories: dowLabels || [],
      },
      yaxis: {
        title: { text: "Average" },
      },
    };

    const peakHourSeries = [
      {
        name: "Visits",
        data: peakHours.map((item) => item.count || 0),
      },
    ];

    const peakHourOptions = {
      chart: { type: "line" },
      colors: [ANALYTICS_COLORS.accent],
      fill: {
        type: "gradient",
        gradient: {
          shadeIntensity: 1,
          opacityFrom: 0.26,
          opacityTo: 0.03,
          stops: [0, 100],
        },
      },
      markers: {
        size: 4,
        strokeWidth: 0,
        hover: { sizeOffset: 2 },
      },
      xaxis: {
        categories: peakHours.map((item) => formatHourLabel(item.hour)),
        tickAmount: Math.min(peakHours.length, 8),
      },
      yaxis: {
        title: { text: "Visits" },
      },
    };

    const programSeries = [
      {
        name: "Visits",
        data: programDistribution.slice(0, 6).map((item) => item.count || 0),
      },
    ];

    const programOptions = {
      chart: { type: "bar" },
      colors: [ANALYTICS_COLORS.rose],
      plotOptions: {
        bar: {
          horizontal: true,
          borderRadius: 8,
          barHeight: "60%",
        },
      },
      xaxis: {
        categories: programDistribution.slice(0, 6).map((item) => item.program || "Unknown"),
      },
      legend: { show: false },
    };

    const genderSeries = genderData.filter((item) => item.count > 0).map((item) => item.count || 0);
    const genderOptions = {
      chart: { type: "donut" },
      colors: ["#2563eb", "#0f766e", "#f97316", "#a855f7", "#e11d48"],
      labels: genderData.filter((item) => item.count > 0).map((item) => item.gender || "Unknown"),
      legend: { position: "bottom" },
      plotOptions: {
        pie: {
          donut: {
            size: "68%",
            labels: {
              show: true,
              total: {
                show: true,
                label: "Profiles",
                formatter: () => `${genderSeries.reduce((sum, value) => sum + value, 0)}`,
              },
            },
          },
        },
      },
    };

    const yearSeries = [
      {
        name: "Students",
        data: yearLevelData.slice(0, 6).map((item) => item.count || 0),
      },
    ];

    const yearOptions = {
      chart: { type: "radar" },
      colors: [ANALYTICS_COLORS.gold],
      xaxis: {
        categories: yearLevelData.slice(0, 6).map((item) => getYearLevelLabel(item)),
      },
      markers: { size: 4 },
      fill: {
        type: "solid",
        opacity: 0.24,
      },
      tooltip: {
        y: {
          formatter: (value, { dataPointIndex }) => {
            const yearData = yearLevelData.slice(0, 6);
            const item = yearData[dataPointIndex];
            const label = item?.year_level || "Unknown";
            return `${label}: ${value} students`;
          },
        },
      },
    };

    return (
      <section className="section">
        <div className="pagetitle">
          <h1>Live Analytics</h1>
          <nav>
            <ol className="breadcrumb mb-0">
              <li className="breadcrumb-item">
                <a href="/dashboard">Home</a>
              </li>
              <li className="breadcrumb-item active">Live Analytics</li>
            </ol>
          </nav>
        </div>

        <div
          ref={headerRef}
          style={{
            position: "sticky",
            top: APP_HEADER_HEIGHT,
            zIndex: 100,
            background: "#f6f7fb",
            padding: "12px 0 14px",
            transition: "box-shadow 0.2s",
            marginBottom: 18,
          }}
        >
          <div
            style={{
              background: "rgba(255,255,255,0.82)",
              borderRadius: 22,
              padding: "16px 20px",
              border: `1px solid ${ANALYTICS_COLORS.border}`,
              boxShadow: "0 14px 35px rgba(15, 23, 42, 0.08)",
              backdropFilter: "blur(12px)",
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <div>
                <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                  <div
                    style={{
                      width: 34,
                      height: 34,
                      borderRadius: 12,
                      background: "linear-gradient(135deg, rgba(37,99,235,0.16), rgba(15,118,110,0.16))",
                      color: ANALYTICS_COLORS.primary,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 16,
                    }}
                  >
                    <i className="bi bi-bar-chart-line"></i>
                  </div>
                  <div>
                    <h5 className="card-title" style={{ padding: 0, margin: 0, color: ANALYTICS_COLORS.ink }}>
                      Live Analytics Studio
                    </h5>
                  </div>
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: ANALYTICS_COLORS.muted,
                    marginTop: 8,
                    display: "flex",
                    gap: 8,
                    flexWrap: "wrap",
                    alignItems: "center",
                  }}
                >
                  <span
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      padding: "4px 10px",
                      borderRadius: 999,
                      background: socketConnected ? "rgba(25,135,84,0.1)" : "rgba(255,193,7,0.12)",
                      color: socketConnected ? "#198754" : "#a06a00",
                      fontWeight: 600,
                    }}
                  >
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: socketConnected ? "#198754" : "#ffc107",
                      }}
                    ></span>
                    {socketConnected ? "Live connection active" : "Fallback polling mode"}
                  </span>
                  <span>Last sync: {lastUpdatedLabel}</span>
                  <span>Descriptive analytics with interpretation-first visuals</span>
                </div>
              </div>
              <div className="d-flex align-items-center gap-2 flex-wrap">
                <button
                  className="btn btn-sm btn-outline-primary d-flex align-items-center gap-1 px-2 py-1"
                  onClick={() => runAnalyticsPipeline({ force: true })}
                  disabled={loading || refreshing}
                >
                  <i className="bi bi-arrow-clockwise"></i>
                  {loading || refreshing ? "Syncing..." : "Sync Now"}
                </button>
                {canManageImports ? (
                  <ImportModal
                    onImportSuccess={() => {
                      runAnalyticsPipeline({ force: true });
                    }}
                  />
                ) : null}
              </div>
            </div>
          </div>
        </div>

        {error ? (
          <div className="alert alert-danger">
            <i className="bi bi-exclamation-triangle me-2"></i>
            Failed to refresh analytics. Showing the latest available descriptive data.
          </div>
        ) : null}

        <div
          style={{
            background: heroGradient,
            borderRadius: 30,
            padding: "26px 24px",
            boxShadow: "0 30px 70px rgba(15, 23, 42, 0.18)",
            overflow: "hidden",
            position: "relative",
            marginBottom: 20,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 18,
              flexWrap: "wrap",
              position: "relative",
              zIndex: 2,
            }}
          >
            <div style={{ flex: "1 1 320px", maxWidth: 640 }}>
              <div
                style={{
                  fontSize: 12,
                  letterSpacing: "0.12em",
                  textTransform: "uppercase",
                  color: "rgba(255,255,255,0.72)",
                  fontWeight: 700,
                  marginBottom: 10,
                }}
              >
                Descriptive intelligence
              </div>
              <div
                style={{
                  color: "#fff",
                  fontSize: 34,
                  fontWeight: 900,
                  lineHeight: 1.04,
                  maxWidth: 540,
                }}
              >
                Read visitor behavior at a glance with richer visuals and direct interpretation.
              </div>
              <div
                style={{
                  color: "rgba(255,255,255,0.8)",
                  marginTop: 14,
                  lineHeight: 1.7,
                  fontSize: 14,
                  maxWidth: 560,
                }}
              >
                This view is focused on descriptive analytics only: observed usage patterns,
                attendance rhythm, visitor composition, and plain-language findings from live and
                imported library records.
              </div>
            </div>

            <div
              style={{
                flex: "1 1 340px",
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))",
                gap: 14,
                alignSelf: "stretch",
              }}
            >
              <SummaryStat
                label="Clean Visits"
                value={fmt(currentData?.total_cleaned_logs || 0)}
                tone={ANALYTICS_COLORS.primary}
                icon="bi bi-activity"
                helper="Unique daily visits retained after cleaning"
              />
              <SummaryStat
                label="Visit Mean"
                value={fmt(stats?.mean_daily_visits || 0)}
                tone={ANALYTICS_COLORS.secondary}
                icon="bi bi-graph-up"
                helper="Average visits per recorded day"
              />
              <SummaryStat
                label="Peak Weekday"
                value={peakDow.label}
                tone={ANALYTICS_COLORS.accent}
                icon="bi bi-calendar2-week"
                helper={`${peakDow.value} average visits`}
              />
              <SummaryStat
                label="Quality Score"
                value={`${dq?.quality_score || 0}%`}
                tone={ANALYTICS_COLORS.rose}
                icon="bi bi-shield-check"
                helper={`${fmt(totalRemoved)} records removed`}
              />
            </div>
          </div>
          <div
            style={{
              position: "absolute",
              inset: 0,
              background:
                "radial-gradient(circle at top right, rgba(255,255,255,0.16), transparent 34%), radial-gradient(circle at bottom left, rgba(255,255,255,0.1), transparent 28%)",
              pointerEvents: "none",
            }}
          ></div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
            gap: 14,
            marginBottom: 20,
          }}
        >
          {insightCards.map((item) => (
            <InsightChip key={item.title} {...item} />
          ))}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "minmax(0, 1.8fr) minmax(300px, 1fr)",
            gap: 18,
            marginBottom: 18,
          }}
        >
          <ApexChartPanel
            title="30-Day Visitor Flow"
            subtitle={`Observed visitor counts across the last 30 days · ${last30Total} total visits recorded in this window`}
            height={340}
            options={visitsTrendOptions}
            series={visitsTrendSeries}
          />
          <div
            style={{
              background: "#fff",
              borderRadius: 24,
              border: `1px solid ${ANALYTICS_COLORS.border}`,
              padding: 22,
              boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)",
            }}
          >
            <div style={{ fontSize: 17, fontWeight: 800, color: ANALYTICS_COLORS.ink }}>
              Visual Readout
            </div>
            <div style={{ marginTop: 12, color: ANALYTICS_COLORS.muted, lineHeight: 1.75, fontSize: 13 }}>
              <p style={{ margin: 0 }}>
                The month is currently showing a <strong style={{ color: ANALYTICS_COLORS.ink }}>{trendDirection}</strong>{" "}
                pattern, with an average of <strong style={{ color: ANALYTICS_COLORS.ink }}>{stats?.mean_daily_visits || 0}</strong> visits per recorded day.
              </p>
              <p style={{ margin: "12px 0 0" }}>
                The most active weekday is <strong style={{ color: ANALYTICS_COLORS.ink }}>{peakDow.label}</strong>,
                while <strong style={{ color: ANALYTICS_COLORS.ink }}>{peakHour.label}</strong> is the strongest arrival hour in the cleaned dataset.
              </p>
              <p style={{ margin: "12px 0 0" }}>
                <strong style={{ color: ANALYTICS_COLORS.ink }}>{topProgram.label}</strong> currently appears most often in the recorded visits, making it the clearest program-level contributor.
              </p>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 12,
                marginTop: 16,
              }}
            >
              <div style={{ fontSize: 18, fontWeight: 800, color: ANALYTICS_COLORS.primary }}>
                {fmt(stats?.max_daily_visits || 0)}
              </div>
              <div style={{ fontSize: 18, fontWeight: 800, color: ANALYTICS_COLORS.secondary }}>
                {fmt(stats?.std_dev || 0)}
              </div>
              <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, marginTop: -8 }}>
                Highest observed daily total
              </div>
              <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, marginTop: -8 }}>
                Daily variability
              </div>
            </div>
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 18,
            marginBottom: 18,
          }}
        >
          <ApexChartPanel
            title="Attendance by Weekday"
            subtitle={`Weekday average is ${weekdayAverage} visits · ${peakDow.label} leads the pattern`}
            height={300}
            options={dowOptions}
            series={dowSeries}
          />
          <ApexChartPanel
            title="Visitor Rhythm by Hour"
            subtitle={`Peak hour is ${peakHour.label} · ideal for staffing and assistance planning`}
            height={300}
            options={peakHourOptions}
            series={peakHourSeries}
          />
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 18,
            marginBottom: 18,
          }}
        >
          <ApexChartPanel
            title="Top Programs by Visits"
            subtitle={`${topProgram.label} currently leads the descriptive program distribution`}
            height={320}
            options={programOptions}
            series={programSeries}
          />
          {genderSeries.length > 0 ? (
            <ApexChartPanel
              title="Gender Composition"
              subtitle="Distribution of recorded visitors where gender data is available"
              height={320}
              options={genderOptions}
              series={genderSeries}
            />
          ) : (
            <div
              style={{
                background: "#fff",
                borderRadius: 24,
                border: `1px solid ${ANALYTICS_COLORS.border}`,
                padding: 22,
                boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)",
              }}
            >
              <div style={{ fontSize: 17, fontWeight: 800, color: ANALYTICS_COLORS.ink }}>
                Gender Composition
              </div>
              <div style={{ marginTop: 12, color: ANALYTICS_COLORS.muted, lineHeight: 1.75, fontSize: 13 }}>
                Gender breakdown is still sparse in the current dataset. As more imported historical records include gender values, this panel will become more representative.
              </div>
            </div>
          )}
        </div>

        {yearSeries[0]?.data?.length > 0 ? (
          <div style={{ marginBottom: 18 }}>
            <ApexChartPanel
              title="Year Level Spread"
              subtitle="A radar view of where descriptive attendance is concentrated across year levels"
              height={330}
              options={yearOptions}
              series={yearSeries}
            />
          </div>
        ) : null}

        <div
          style={{
            background: "#fff",
            borderRadius: 24,
            border: `1px solid ${ANALYTICS_COLORS.border}`,
            padding: 22,
            boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)",
          }}
        >
          <div style={{ fontSize: 17, fontWeight: 800, color: ANALYTICS_COLORS.ink, marginBottom: 12 }}>
            Data Quality and Interpretation Notes
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
              gap: 12,
            }}
          >
            <div style={{ background: "#f8fafc", borderRadius: 18, padding: 16, border: `1px solid ${ANALYTICS_COLORS.border}` }}>
              <div style={{ fontSize: 12, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 700 }}>
                Collection
              </div>
              <div style={{ marginTop: 8, fontSize: 14, color: ANALYTICS_COLORS.ink, fontWeight: 800 }}>
                {fmt(dq?.total_live || 0)} live + {fmt(dq?.total_imported || 0)} imported
              </div>
              <div style={{ marginTop: 6, fontSize: 12.5, color: ANALYTICS_COLORS.muted, lineHeight: 1.65 }}>
                The analytics stream merges kiosk recognition logs and imported history into one descriptive reporting dataset.
              </div>
            </div>
            <div style={{ background: "#f8fafc", borderRadius: 18, padding: 16, border: `1px solid ${ANALYTICS_COLORS.border}` }}>
              <div style={{ fontSize: 12, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 700 }}>
                Cleaning
              </div>
              <div style={{ marginTop: 8, fontSize: 14, color: ANALYTICS_COLORS.ink, fontWeight: 800 }}>
                {fmt(totalRemoved)} records removed
              </div>
              <div style={{ marginTop: 6, fontSize: 12.5, color: ANALYTICS_COLORS.muted, lineHeight: 1.65 }}>
                Low-confidence, outside-hours, and duplicate same-day scans are filtered so each student counts once per day.
              </div>
            </div>
            <div style={{ background: "#f8fafc", borderRadius: 18, padding: 16, border: `1px solid ${ANALYTICS_COLORS.border}` }}>
              <div style={{ fontSize: 12, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 700 }}>
                Reliability
              </div>
              <div style={{ marginTop: 8, fontSize: 14, color: ANALYTICS_COLORS.ink, fontWeight: 800 }}>
                {dq?.quality_score || 0}% quality score
              </div>
              <div style={{ marginTop: 6, fontSize: 12.5, color: ANALYTICS_COLORS.muted, lineHeight: 1.65 }}>
                The page emphasizes descriptive reading only, so decisions are grounded in observed behavior instead of projections.
              </div>
            </div>
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>Live Analytics</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item active">Live Analytics</li>
          </ol>
        </nav>
      </div>

      {/* Show spinner for descriptive analytics loading */}
      {loading && !basicData && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "500px",
            gap: 16,
          }}
        >
          <div
            style={{
              width: 48,
              height: 48,
              borderRadius: "50%",
              border: "4px solid #e9ecef",
              borderTop: "4px solid #0d6efd",
              animation: "spin 1s linear infinite",
            }}
          ></div>
          <div style={{ fontSize: 14, color: "#6c757d", fontWeight: 500 }}>
            Loading descriptive analytics...
          </div>
          <style>{`
            @keyframes spin {
              to { transform: rotate(360deg); }
            }
          `}</style>
        </div>
      )}

      {initialLoadFailed && (
        <div className="alert alert-danger">
          <i className="bi bi-exclamation-triangle me-2"></i>
          {errorMessage || "Failed to load analytics data. Check the analytics API or try syncing again."}
        </div>
      )}

      {!loading && !hasRenderableData && !initialLoadFailed && (
        <div className="alert alert-info">
          <i className="bi bi-info-circle me-2"></i>
          No analytics content is available yet. Import historical logs or wait for live recognition activity.
        </div>
      )}

      {/* Show content once basic data is available */}
      {hasRenderableData && (
        <>
          {/* Sticky header - always visible */}
          <div
            ref={headerRef}
            style={{
              position: "sticky",
              top: APP_HEADER_HEIGHT,
              zIndex: 100,
              background: "#f6f7fb",
              padding: "12px 0 10px",
              transition: "box-shadow 0.2s",
              marginBottom: 16,
            }}
          >
            <div
              style={{
                background: "#fff",
                borderRadius: 12,
                padding: "14px 20px",
                border: "1px solid #e9ecef",
                boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "flex-start",
                  gap: 12,
                  flexWrap: "wrap",
                }}
              >
                <div>
                  <h5 className="card-title" style={{ padding: 0, margin: 0 }}>
                    Realtime Library Activity
                  </h5>
                  <div
                    style={{
                      fontSize: 12,
                      color: "#6c757d",
                      marginTop: 4,
                      display: "flex",
                      gap: 8,
                      flexWrap: "wrap",
                      alignItems: "center",
                    }}
                  >
                    <span
                      style={{
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 6,
                        padding: "4px 10px",
                        borderRadius: 999,
                        background: socketConnected ? "rgba(25,135,84,0.1)" : "rgba(255,193,7,0.12)",
                        color: socketConnected ? "#198754" : "#a06a00",
                        fontWeight: 600,
                      }}
                    >
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: "50%",
                          background: socketConnected ? "#198754" : "#ffc107",
                        }}
                      ></span>
                      {socketConnected ? "Live connection active" : "Fallback polling mode"}
                    </span>
                    <span>Last sync: {lastUpdatedLabel}</span>
                    <span>Auto-refreshes on analytics events</span>
                  </div>
                </div>
                <div className="d-flex align-items-center gap-2 flex-wrap">
                  <button
                    className="btn btn-sm btn-outline-primary d-flex align-items-center gap-1 px-2 py-1"
                    onClick={() => runAnalyticsPipeline({ force: true })}
                    disabled={loading || refreshing}
                  >
                    <i className="bi bi-arrow-clockwise"></i>
                    {loading || refreshing ? "Syncing..." : "Sync Now"}
                  </button>
                  {canManageImports ? (
                    <ImportModal
                      onImportSuccess={() => {
                        runAnalyticsPipeline({ force: true });
                      }}
                    />
                  ) : null}
                </div>
              </div>
            </div>
          </div>

          {error && (
            <div className="alert alert-danger">
              <i className="bi bi-exclamation-triangle me-2"></i>
              {errorMessage || "Failed to refresh analytics. Showing the latest available descriptive data."}
            </div>
          )}

          {noAnalyticsData && (
            <div className="alert alert-info">
              <i className="bi bi-info-circle me-2"></i>
              {currentData?.message || currentData?.error || "No analytics data available."} Import historical logs or collect live recognition logs to generate analytics.
            </div>
          )}

          {!noAnalyticsData && (
            <>
              {/* Stage 1 */}
              <Section
            stepNum="1"
            title="Data Collection"
            color="#0d6efd"
            subtitle={`${fmt(dq?.total_live || 0)} live · ${fmt(dq?.total_imported || 0)} imported · ${fmt(currentData?.total_students || 0)} students`}
          >
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "repeat(4,1fr)",
                gap: 12,
                marginBottom: 4,
              }}
            >
              {[
                {
                  label: "Registered Students",
                  value: fmt(currentData?.total_students || 0),
                  color: "#0d6efd",
                  icon: "bi-people",
                },
                {
                  label: "Live Logs",
                  value: fmt(dq?.total_live || 0),
                  color: "#198754",
                  icon: "bi-camera-video",
                },
                {
                  label: "Imported Records",
                  value: fmt(dq?.total_imported || 0),
                  color: "#6f42c1",
                  icon: "bi-cloud-upload",
                },
                {
                  label: "Combined Total",
                  value: fmt((dq?.total_live || 0) + (dq?.total_imported || 0)),
                  color: "#fd7e14",
                  icon: "bi-layers",
                },
              ].map((m, i) => (
                <div
                  key={i}
                  style={{
                    background: "#f8f9fa",
                    borderRadius: 10,
                    padding: "14px 16px",
                    border: "1px solid #e9ecef",
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                  }}
                >
                  <div
                    style={{
                      width: 36,
                      height: 36,
                      borderRadius: 10,
                      background: m.color + "15",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      flexShrink: 0,
                    }}
                  >
                    <i
                      className={`bi ${m.icon}`}
                      style={{ color: m.color, fontSize: 16 }}
                    ></i>
                  </div>
                  <div>
                    <div style={{ fontSize: 10.5, color: "#aaa" }}>
                      {m.label}
                    </div>
                    <div
                      style={{ fontSize: 18, fontWeight: 800, color: m.color }}
                    >
                      {m.value}
                    </div>
                  </div>
                </div>
              ))}
            </div>
            <Interpretation icon="bi-database" color="#0d6efd">
              <strong>Data Collection Summary:</strong> The analytics pipeline
              combines <strong>{fmt(dq?.total_live || 0)}</strong> live face
              recognition logs with{" "}
              <strong>{fmt(dq?.total_imported || 0)}</strong> historically
              imported records, resulting in{" "}
              <strong>
                {fmt((dq?.total_live || 0) + (dq?.total_imported || 0))}
              </strong>{" "}
              total entries spanning{" "}
              <strong>{currentData?.total_students || 0}</strong> registered students.
              This merged dataset serves as the foundation for all downstream
              analysis.
            </Interpretation>
          </Section>

          {/* Stage 2 */}
          <Section
            stepNum="2"
            title="Data Cleaning"
            color="#dc3545"
            subtitle={`${fmt(totalRemoved)} records removed · ${dq?.quality_score || 0}% retained`}
          >
            <DataQualitySection dq={dq} />
          </Section>

          {/* Stage 3 */}
          <Section
            stepNum="3"
            title="Data Transformation"
            color="#6c757d"
            subtitle="Deduplication · Time feature extraction · Visit frequency computation"
          >
            <div
              style={{
                background: "#f8f9fa",
                borderRadius: 10,
                padding: "14px 16px",
                border: "1px solid #e9ecef",
                marginBottom: 4,
              }}
            >
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 12,
                }}
              >
                {[
                  {
                    icon: "bi-calendar-check",
                    color: "#0d6efd",
                    text: "Each raw log transformed into one unique daily visit per student (first scan per day kept)",
                  },
                  {
                    icon: "bi-clock",
                    color: "#6f42c1",
                    text: "Time features extracted: hour of day, day of week, week number, month, year",
                  },
                  {
                    icon: "bi-person-check",
                    color: "#198754",
                    text: "Visit frequency computed per student across the full data range",
                  },
                  {
                    icon: "bi-union",
                    color: "#fd7e14",
                    text: "Live and imported records merged into a single unified dataset before analysis",
                  },
                ].map((t, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      gap: 10,
                      alignItems: "flex-start",
                    }}
                  >
                    <div
                      style={{
                        width: 28,
                        height: 28,
                        borderRadius: 7,
                        background: t.color + "15",
                        display: "flex",
                        alignItems: "center",
                        justifyContent: "center",
                        flexShrink: 0,
                      }}
                    >
                      <i
                        className={`bi ${t.icon}`}
                        style={{ color: t.color, fontSize: 13 }}
                      ></i>
                    </div>
                    <span
                      style={{ fontSize: 12.5, color: "#555", lineHeight: 1.6 }}
                    >
                      {t.text}
                    </span>
                  </div>
                ))}
              </div>
            </div>
            <Interpretation icon="bi-arrow-repeat" color="#6c757d">
              <strong>Transformation Interpretation:</strong> Raw recognition
              events were consolidated so each student counts as exactly{" "}
              <strong>one visit per day</strong>. Additional time-based features
              were derived from each timestamp to enable day-of-week, hourly,
              and monthly pattern analysis.
            </Interpretation>
          </Section>

          {/* Stage 4 */}
          <Section
            stepNum="4"
            title="Exploratory Data Analysis"
            color="#0dcaf0"
            subtitle={`Mean: ${stats?.mean_daily_visits || 0} visits/day · Std Dev: ${stats?.std_dev || 0}`}
          >
            <EDASection
              stats={stats}
              dowLabels={dowLabels}
              dowAverages={dowAverages}
            />
          </Section>

          {/* Stage 4b */}
          <Section
            stepNum="4b"
            title="30-Day Visitor Trend"
            color="#0d6efd"
            subtitle="Cleaned daily visits over last 30 days with mean reference line"
            defaultOpen={false}
          >
            <TrendChart
              labels={last30Labels}
              counts={last30Counts}
            />
          </Section>

          {/* Stage 6 */}
          <div
            style={{
              background: "rgba(25,135,84,0.06)",
              border: "1px solid rgba(25,135,84,0.2)",
              borderRadius: 12,
              padding: "14px 20px",
              display: "flex",
              alignItems: "center",
              gap: 12,
              fontSize: 13,
            }}
          >
            <i className="bi bi-check-circle-fill text-success fs-5"></i>
            <span>
              <strong>Analytics view is live and descriptive only.</strong>{" "}
              The dashboard is currently summarizing <strong>{fmt(dq?.total_raw || 0)}</strong> raw
              records into <strong>{fmt(currentData?.total_cleaned_logs || 0)}</strong>{" "}
              clean unique daily visits, with a current quality score of{" "}
              <strong
                style={{
                  color: dq?.quality_score >= 90 ? "#198754" : "#ffc107",
                }}
              >
                {dq?.quality_score || 0}%
              </strong>
              .
            </span>
          </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

export default function AnalyticsReports() {
  return (
    <ErrorBoundary>
      <AnalyticsReportsInner />
    </ErrorBoundary>
  );
}





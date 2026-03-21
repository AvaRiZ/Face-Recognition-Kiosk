import React from "react";
import { fetchJson } from "../api.js";

// ── Helpers ───────────────────────────────────────────────────
function fmt(n) {
  return (n ?? 0).toLocaleString();
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

  async function uploadFile(file) {
    if (!file.name.endsWith(".csv")) {
      setResult({ success: false, message: "Only CSV files accepted." });
      return;
    }
    setUploading(true);
    setResult(null);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch("/api/import-logs", { method: "POST", body: fd });
      const d = await r.json();
      setResult(d);
      if (d.success) {
        const s = await fetchJson("/api/import-logs/summary");
        setSummary(s);
        if (onImportSuccess) onImportSuccess();
      }
    } catch {
      setResult({ success: false, message: "Upload failed." });
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }
  async function deleteBatch(id) {
    if (!confirm("Delete this batch?")) return;
    await fetch(`/api/import-logs/delete/${id}`, { method: "POST" });
    const s = await fetchJson("/api/import-logs/summary");
    setSummary(s);
    if (onImportSuccess) onImportSuccess();
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
      {showModal && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 1055,
            background: "rgba(0,0,0,0.4)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 16,
            backdropFilter: "blur(3px)",
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
                      {result.inserted} imported
                      {result.skipped > 0 ? ` · ${result.skipped} skipped` : ""}
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
      )}
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
function ForecastSection({ forecast }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !forecast?.values?.length)
      return;
    if (chartRef.current) chartRef.current.destroy();
    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels: forecast.labels,
        datasets: [
          {
            label: "Predicted",
            data: forecast.values,
            backgroundColor: "rgba(13,110,253,0.75)",
            borderRadius: 8,
            borderWidth: 0,
            order: 2,
          },
          {
            label: "Upper",
            data: forecast.upper,
            type: "line",
            borderColor: "rgba(220,53,69,0.45)",
            borderWidth: 1.5,
            borderDash: [4, 3],
            pointRadius: 0,
            fill: false,
            order: 1,
          },
          {
            label: "Lower",
            data: forecast.lower,
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
  }, [forecast]);

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
    forecast.labels[forecast.values.indexOf(Math.max(...forecast.values))];
  const quietDay =
    forecast.labels[forecast.values.indexOf(Math.min(...forecast.values))];
  const totalWeek = forecast.values.reduce((a, b) => a + b, 0);
  const avgRange =
    forecast.values
      .map((v, i) => forecast.upper[i] - forecast.lower[i])
      .reduce((a, b) => a + b, 0) / forecast.values.length;

  return (
    <div>
      <div
        style={{
          background: "rgba(255,193,7,0.08)",
          border: "1px solid rgba(255,193,7,0.2)",
          borderRadius: 8,
          padding: "8px 14px",
          marginBottom: 16,
          fontSize: 12,
        }}
      >
        <i className="bi bi-cpu text-warning me-1"></i>
        <strong>Method:</strong> {forecast.method}
        {forecast.aic && (
          <span style={{ marginLeft: 12, color: "#888" }}>
            AIC: <strong>{forecast.aic}</strong> · BIC:{" "}
            <strong>{forecast.bic}</strong> · Order:{" "}
            <code style={{ fontSize: 11 }}>
              ARIMA({forecast.model_order?.join(",")})
            </code>
          </span>
        )}
      </div>
      <div style={{ height: 220, position: "relative", marginBottom: 16 }}>
        <canvas ref={canvasRef}></canvas>
      </div>
      <div style={{ overflowX: "auto", marginBottom: 4 }}>
        <table
          style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}
        >
          <thead>
            <tr style={{ background: "#f8f9fa" }}>
              {["Day", "Predicted", "95% Range"].map((h, i) => (
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
            {forecast.labels.map((l, i) => (
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
                    color: "#0d6efd",
                  }}
                >
                  {forecast.values[i]}
                </td>
                <td
                  style={{
                    padding: "7px 12px",
                    textAlign: "center",
                    border: "1px solid #e9ecef",
                    color: "#aaa",
                  }}
                >
                  {forecast.lower[i]}–{forecast.upper[i]}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Interpretation icon="bi-graph-up-arrow" color="#ffc107">
        <strong>Forecast Interpretation:</strong>{" "}
        {forecast.aic
          ? `The ARIMA(${forecast.model_order?.join(",")}) model was auto-selected by lowest AIC (${forecast.aic}). `
          : ""}
        The model predicts approximately <strong>{totalWeek}</strong> visits
        over the next 7 days, peaking on{" "}
        <strong style={{ color: "#dc3545" }}>{peakDay}</strong> and lowest on{" "}
        <strong style={{ color: "#198754" }}>{quietDay}</strong>. Average 95%
        confidence interval: ±{Math.round(avgRange / 2)} visits/day
        {avgRange < 10
          ? " — high forecast precision."
          : " — moderate uncertainty, verify against academic calendar."}{" "}
        Forecasted Saturday/Sunday values should be treated as closed-library
        days.
      </Interpretation>
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
    if (!canvasRef.current || !window.Chart || !hasRegressionData)
      return;
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

// ── Main Page ─────────────────────────────────────────────────
export default function AnalyticsReports() {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(false);
  const [error, setError] = React.useState(false);
  const [hasRun, setHasRun] = React.useState(false);
  const [activeStep, setActive] = React.useState(1);
  const headerRef = React.useRef(null);

  React.useEffect(() => {
    if (!loading) return undefined;
    setActive(1);
    let step = 1;
    const timer = window.setInterval(() => {
      step = step >= 6 ? 6 : step + 1;
      setActive(step);
      if (step >= 6) window.clearInterval(timer);
    }, 550);
    return () => window.clearInterval(timer);
  }, [loading]);

  async function runAnalyticsPipeline() {
    setLoading(true);
    setError(false);
    setActive(1);
    try {
      const d = await fetchJson("/api/analytics-reports");
      setData(d);
      setActive(6);
      setHasRun(true);
    } catch {
      setError(true);
      setActive(1);
    } finally {
      setLoading(false);
    }
  }

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

  const dq = data?.data_quality;
  const stats = data?.descriptive_stats;
  const regression = data?.regression ?? {};
  const clustering = data?.clustering ?? {};
  const chiSquare = data?.chi_square ?? {};
  const correlation = data?.correlation ?? {};
  const anova = data?.anova ?? {};

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>Analytics &amp; Reports</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item active">Analytics &amp; Reports</li>
          </ol>
        </nav>
      </div>

      {/* Sticky header */}
      <div
        ref={headerRef}
        style={{
          position: "sticky",
          top: 0,
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
              alignItems: "center",
              gap: 12,
              flexWrap: "wrap",
              marginBottom: 14,
            }}
          >
            <h5 className="card-title" style={{ padding: 0, margin: 0 }}>
              Data Pipeline
            </h5>
            <div className="d-flex align-items-center gap-2 flex-wrap">
              <button
                className="btn btn-sm btn-primary d-flex align-items-center gap-1 px-2 py-1"
                onClick={runAnalyticsPipeline}
                disabled={loading}
              >
                {loading ? (
                  <>
                    <span
                      className="spinner-border spinner-border-sm"
                      role="status"
                      aria-hidden="true"
                    ></span>
                    Running...
                  </>
                ) : (
                  <>
                    <i className="bi bi-play-circle"></i>
                    {hasRun ? "Run Again" : "Run Pipeline"}
                  </>
                )}
              </button>
              <ImportModal
                onImportSuccess={() => {
                  if (hasRun) runAnalyticsPipeline();
                }}
              />
            </div>
          </div>
          <PipelineStepper activeStep={activeStep} isLoading={loading} />
        </div>
      </div>

      {error && (
        <div className="alert alert-danger">
          <i className="bi bi-exclamation-triangle me-2"></i>
          Failed to load analytics. Please try running the pipeline again.
        </div>
      )}

      {!hasRun && !loading && !error && (
        <div
          style={{
            background: "#fff",
            borderRadius: 12,
            border: "1px solid #e9ecef",
            padding: "32px 24px",
            textAlign: "center",
            boxShadow: "0 1px 4px rgba(0,0,0,0.04)",
            marginBottom: 16,
          }}
        >
          <div
            style={{
              width: 56,
              height: 56,
              borderRadius: 16,
              background: "rgba(13,110,253,0.08)",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              color: "#0d6efd",
              fontSize: 24,
              marginBottom: 12,
            }}
          >
            <i className="bi bi-cpu"></i>
          </div>
          <h5 style={{ color: "#1a1a2e", marginBottom: 8 }}>
            Analytics pipeline is ready
          </h5>
          <p style={{ color: "#6c757d", marginBottom: 16 }}>
            Run the pipeline when you want to generate the latest analytics and
            report sections.
          </p>
          <button className="btn btn-primary" onClick={runAnalyticsPipeline}>
            <i className="bi bi-play-circle me-1"></i>Run Analytics Pipeline
          </button>
        </div>
      )}

      {loading && !hasRun && (
        <div
          className="d-flex justify-content-center align-items-center"
          style={{ minHeight: "30vh", marginBottom: 16 }}
        >
          <div style={{ textAlign: "center" }}>
            <div
              className="spinner-border text-primary mb-3"
              role="status"
            ></div>
            <div style={{ fontSize: 13, color: "#aaa" }}>
              Running analytics pipeline...
            </div>
          </div>
        </div>
      )}

      {data && (
        <>
          {/* Stage 1 */}
          <Section
            stepNum="1"
            title="Data Collection"
            color="#0d6efd"
            subtitle={`${fmt(dq?.total_live || 0)} live · ${fmt(dq?.total_imported || 0)} imported · ${fmt(data?.total_students || 0)} students`}
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
                  value: fmt(data?.total_students || 0),
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
              <strong>{data?.total_students || 0}</strong> registered students.
              This merged dataset serves as the foundation for all downstream
              analysis.
            </Interpretation>
          </Section>

          {/* Stage 2 */}
          <Section
            stepNum="2"
            title="Data Cleaning"
            color="#dc3545"
            subtitle={`${fmt(dq?.total_removed || 0)} records removed · ${dq?.quality_score || 0}% retained`}
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
              dowLabels={data?.dow_labels}
              dowAverages={data?.dow_averages}
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
              labels={data?.last_30_labels}
              counts={data?.last_30_counts}
            />
          </Section>

          {/* Stage 5a — Forecast */}
          <Section
            stepNum="5a"
            title="7-Day Forecast"
            color="#ffc107"
            subtitle={`${data?.forecast?.method?.includes("ARIMA") ? "ARIMA model" : "Moving average"} · Predicted visits next 7 days`}
            defaultOpen={false}
          >
            <ForecastSection forecast={data?.forecast} />
          </Section>

          {/* Stage 5b — Linear Regression (NEW) */}
          <Section
            stepNum="5b"
            title="Linear Regression — Trend Analysis"
            color="#dc3545"
            subtitle={`R²=${data?.regression?.r2 ?? "—"} · Trend: ${data?.regression?.trend ?? "—"}`}
            defaultOpen={false}
          >
            <LinearRegressionSection
              regression={regression}
              interpretation={data?.regression_interpretation}
              counts={data?.last_30_counts}
              labels={data?.last_30_labels}
            />
          </Section>

          {/* Stage 5c — K-Means Clustering (NEW) */}
          <Section
            stepNum="5c"
            title="K-Means Clustering — Student Behavior Groups"
            color="#6f42c1"
            subtitle={`k=${data?.clustering?.k ?? "—"} clusters · ${data?.clustering?.cluster_summary?.length ?? 0} groups identified`}
            defaultOpen={false}
          >
            <ClusteringSection
              clustering={clustering}
              interpretation={data?.clustering_interpretation}
            />
          </Section>

          {/* Stage 5d — Statistical Tests (NEW) */}
          <Section
            stepNum="5d"
            title="Statistical Tests — Chi-square · Pearson · ANOVA"
            color="#0dcaf0"
            subtitle="Hypothesis testing across program, gender, and time variables"
            defaultOpen={false}
          >
            <StatisticalTestsSection
              chiSquare={chiSquare}
              chiInterp={data?.chi_square_interpretation}
              correlation={correlation}
              corrInterp={data?.correlation_interpretation}
              anova={anova}
              anovaInterp={data?.anova_interpretation}
            />
          </Section>

          {/* Stage 5e — Segmentation (renumbered from 5b) */}
          <Section
            stepNum="5e"
            title="Student Segmentation"
            color="#198754"
            subtitle={`${data?.segmentation?.regular_count || 0} regular · ${data?.segmentation?.occasional_count || 0} occasional · ${data?.segmentation?.rare_count || 0} rare`}
            defaultOpen={false}
          >
            <SegmentationSection seg={data?.segmentation} />
          </Section>

          {/* Stage 5f — Anomaly Detection (renumbered from 5c) */}
          <Section
            stepNum="5f"
            title="Anomaly Detection"
            color="#dc3545"
            subtitle={`${data?.anomalies?.length || 0} anomalies detected via Z-score`}
            defaultOpen={false}
          >
            <AnomalySection
              anomalies={data?.anomalies}
              mean={stats?.mean_daily_visits || 0}
              stdDev={stats?.std_dev || 0}
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
              <strong>Stage 6 — Visualization &amp; Reporting complete.</strong>{" "}
              Pipeline processed <strong>{fmt(dq?.total_raw || 0)}</strong> raw
              records into <strong>{fmt(data?.total_cleaned_logs || 0)}</strong>{" "}
              clean unique daily visits, achieving a quality score of{" "}
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
    </section>
  );
}

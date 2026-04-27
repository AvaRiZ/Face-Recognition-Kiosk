import React from "react";
import { fetchJson } from "../api.js";
import { socket } from "../socket.js";

// ── Stat Card ────────────────────────────────────────────────
const DASHBOARD_FILTER_OPTIONS = [
  { value: "today", label: "Today" },
  { value: "last_7_days", label: "Last 7 Days" },
  { value: "last_14_days", label: "Last 14 Days" },
  { value: "last_30_days", label: "Last 30 Days" },
  { value: "last_90_days", label: "Last 90 Days" },
];

function formatRangeLabel(startDate, endDate) {
  if (!startDate || !endDate) return "";
  const start = new Date(`${startDate}T00:00:00`);
  const end = new Date(`${endDate}T00:00:00`);
  const formatter = new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  return `${formatter.format(start)} - ${formatter.format(end)}`;
}

function csvEscape(value) {
  const normalized =
    value === null || value === undefined ? "" : String(value).replace(/\r?\n/g, " ");
  if (/[",]/.test(normalized)) {
    return `"${normalized.replace(/"/g, '""')}"`;
  }
  return normalized;
}

function appendCsvSection(lines, title, columns, rows) {
  lines.push([title]);
  lines.push(columns);
  rows.forEach((row) => lines.push(row));
  lines.push([]);
}

function buildDashboardExportCsv(data) {
  const lines = [
    ["Dashboard Export"],
    ["Filter", data?.filter_label ?? ""],
    ["Date Range", formatRangeLabel(data?.filter_start_date, data?.filter_end_date)],
    [],
  ];

  appendCsvSection(
    lines,
    "Summary",
    ["Metric", "Value"],
    [
      ["Registered Students", data?.total_students ?? 0],
      ["Recognition Logs", data?.total_logs ?? 0],
      ["Unique Visitors", data?.unique_visitors ?? 0],
      ["Average Confidence", `${data?.avg_confidence ?? 0}%`],
    ]
  );

  appendCsvSection(
    lines,
    "Daily Visitors",
    ["Date", "Visits"],
    (data?.daily_visitors ?? []).map((item) => [item.date, item.count])
  );

  appendCsvSection(
    lines,
    "Program Distribution",
    ["Program", "Unique Visitors"],
    (data?.program_distribution ?? []).map((item) => [item.program, item.count])
  );

  appendCsvSection(
    lines,
    "Monthly Visitors",
    ["Month", "Visits"],
    (data?.monthly_visitors ?? []).map((item) => [item.month, item.count])
  );

  appendCsvSection(
    lines,
    "Peak Hours",
    ["Hour", "Visits"],
    (data?.peak_hours ?? []).map((count, hour) => [hour, count])
  );

  appendCsvSection(
    lines,
    "Top Visitors",
    ["Name", "SR Code", "Visits"],
    (data?.top_visitors ?? []).map((item) => [item.name, item.sr_code, item.visits])
  );

  const heatmapRows = [];
  (data?.weekly_heatmap ?? []).forEach((entry) => {
    (entry.values ?? []).forEach((count, idx) => {
      heatmapRows.push([entry.day, `${idx + 7}:00`, count]);
    });
  });
  appendCsvSection(lines, "Weekly Heatmap", ["Day", "Hour", "Visits"], heatmapRows);

  return lines.map((row) => row.map(csvEscape).join(",")).join("\n");
}

function downloadDashboardExport(data) {
  const csvContent = buildDashboardExportCsv(data);
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const downloadUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = downloadUrl;
  anchor.download = `dashboard-${data?.filter_key ?? "export"}-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.URL.revokeObjectURL(downloadUrl);
}

function StatCard({ title, value, subtext, iconClass, cardClass }) {
  const highlightStyles = {
    "customers-card": {
      background: "linear-gradient(90deg, rgba(13,110,253,0.9), rgba(13,110,253,0.2))",
    },
    "sales-card": {
      background: "linear-gradient(90deg, rgba(25,135,84,0.9), rgba(25,135,84,0.2))",
    },
    "revenue-card": {
      background: "linear-gradient(90deg, rgba(255,193,7,0.95), rgba(255,193,7,0.2))",
    },
  };

  return (
    <div className="col-md-6 col-xl-3">
      <div
        className={`card info-card ${cardClass}`}
        style={{ position: "relative", overflow: "hidden" }}
      >
        <div
          aria-hidden="true"
          style={{
            position: "absolute",
            top: 0,
            left: 0,
            right: 0,
            height: "4px",
            borderRadius: 0,
            ...highlightStyles[cardClass],
          }}
        />
        <div className="card-body py-3">
          <h5 className="card-title">{title}</h5>
          <div className="d-flex align-items-center">
            <div className="card-icon rounded-circle d-flex align-items-center justify-content-center">
              <i className={iconClass}></i>
            </div>
            <div className="ps-3">
              <h6>{value}</h6>
              <span className="text-muted small">{subtext}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Daily Visitors Line Chart ────────────────────────────────
function DailyVisitorsChart({ data }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !data?.length) return;
    if (chartRef.current) chartRef.current.destroy();

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "line",
      data: {
        labels: data.map((d) => d.date),
        datasets: [
          {
            label: "Visitors",
            data: data.map((d) => d.count),
            borderColor: "#0d6efd",
            backgroundColor: "rgba(13,110,253,0.08)",
            borderWidth: 2,
            pointBackgroundColor: "#0d6efd",
            pointRadius: 4,
            pointHoverRadius: 6,
            fill: true,
            tension: 0.4,
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
              label: (ctx) => ` ${ctx.parsed.y} visitors`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { maxTicksLimit: 7, font: { size: 11 } },
          },
          y: {
            beginAtZero: true,
            ticks: { stepSize: 1, font: { size: 11 } },
            grid: { color: "rgba(0,0,0,0.05)" },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [data]);

  if (!data?.length)
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  return (
    <div
      className="chart-container"
      style={{ height: "220px", position: "relative" }}
    >
      <canvas ref={canvasRef}></canvas>
    </div>
  );
}

// ── Program Distribution Pie Chart ───────────────────────────
function ProgramDistributionChart({ data }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  const COLORS = [
    "#0d6efd",
    "#dc3545",
    "#198754",
    "#ffc107",
    "#0dcaf0",
    "#6f42c1",
    "#fd7e14",
    "#20c997",
  ];

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !data?.length) return;
    if (chartRef.current) chartRef.current.destroy();

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "doughnut",
      data: {
        labels: data.map((d) => d.program || "Unknown"),
        datasets: [
          {
            data: data.map((d) => d.count),
            backgroundColor: COLORS.slice(0, data.length),
            borderWidth: 2,
            borderColor: "#fff",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "60%",
        plugins: {
          legend: {
            position: "bottom",
            labels: { font: { size: 11 }, padding: 12, boxWidth: 12 },
          },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.label}: ${ctx.parsed} students`,
            },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [data]);

  if (!data?.length)
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  return (
    <div
      className="chart-container"
      style={{ height: "220px", position: "relative" }}
    >
      <canvas ref={canvasRef}></canvas>
    </div>
  );
}

// ── Peak Hours Heatmap ───────────────────────────────────────
function PeakHoursChart({ data }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !data?.length) return;
    if (chartRef.current) chartRef.current.destroy();

    const hours = Array.from({ length: 13 }, (_, i) => {
      const hour = i + 7;
      return hour < 12
        ? `${hour} AM`
        : hour === 12
          ? "12 PM"
          : `${hour - 12} PM`;
    });

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels: hours,
        datasets: [
          {
            label: "Visits",
            data: data,
            backgroundColor: data.map((v) => {
              const max = Math.max(...data);
              const intensity = max ? v / max : 0;
              if (intensity > 0.75) return "rgba(220,53,69,0.85)";
              if (intensity > 0.5) return "rgba(255,193,7,0.85)";
              if (intensity > 0.25) return "rgba(13,110,253,0.7)";
              return "rgba(13,110,253,0.25)";
            }),
            borderRadius: 4,
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
            callbacks: {
              title: (ctx) => `${ctx[0].label}`,
              label: (ctx) => ` ${ctx.parsed.y} visits`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            offset: true,
            ticks: {
              autoSkip: false,
              source: "labels", // ✅ force using ALL labels
              font: { size: 9 },
              maxRotation: 45,
            },
          },
          y: {
            beginAtZero: true,
            ticks: { stepSize: 1, font: { size: 11 } },
            grid: { color: "rgba(0,0,0,0.05)" },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [data]);

  if (!data?.length)
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  return (
    <div
      className="chart-container"
      style={{ height: "470px", position: "relative" }}
    >
      <canvas ref={canvasRef}></canvas>
    </div>
  );
}

// ── Top Frequent Visitors Table ──────────────────────────────
function TopVisitorsTable({ data }) {
  if (!data?.length) {
    return (
      <div className="text-center text-muted py-4">
        <i className="bi bi-people fs-3 d-block mb-2"></i>
        No visitor data yet.
      </div>
    );
  }

  const max = data[0]?.visits || 1;

  return (
    <div className="table-responsive">
      <table className="table table-hover align-middle mb-0">
        <thead>
          <tr>
            <th className="text-muted small fw-normal">#</th>
            <th className="text-muted small fw-normal">Student</th>
            <th className="text-muted small fw-normal">SR Code</th>
            <th className="text-muted small fw-normal">Visits</th>
            <th className="text-muted small fw-normal">Frequency</th>
          </tr>
        </thead>
        <tbody>
          {data.map((visitor, i) => (
            <tr key={i}>
              <td className="text-muted small">{i + 1}</td>
              <td>
                <div className="d-flex align-items-center gap-2">
                  <div
                    className="rounded-circle d-flex align-items-center justify-content-center text-white fw-bold"
                    style={{
                      width: 30,
                      height: 30,
                      fontSize: 12,
                      flexShrink: 0,
                      background:
                        i === 0 ? "#dc3545" : i === 1 ? "#fd7e14" : "#0d6efd",
                    }}
                  >
                    {visitor.name?.[0]?.toUpperCase() || "?"}
                  </div>
                  <span className="fw-medium" style={{ fontSize: 13 }}>
                    {visitor.name}
                  </span>
                </div>
              </td>
              <td>
                <code style={{ fontSize: 12 }}>{visitor.sr_code}</code>
              </td>
              <td>
                <span className="badge bg-primary">{visitor.visits}</span>
              </td>
              <td style={{ width: 120 }}>
                <div className="progress" style={{ height: 6 }}>
                  <div
                    className="progress-bar"
                    style={{ width: `${(visitor.visits / max) * 100}%` }}
                  ></div>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Weekly Heatmap ───────────────────────────────────────────
function WeeklyHeatmap({ data }) {
  if (!data?.length) {
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  }

  const days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const hours = Array.from({ length: 13 }, (_, i) => {
    const h = i + 7;
    return h < 12 ? `${h}AM` : h === 12 ? "12PM" : `${h - 12}PM`;
  });

  // Find max value for color scaling
  const allValues = data.flatMap((row) => row.values);
  const max = Math.max(...allValues, 1);

  function getColor(value) {
    if (value === 0) return "#f8f9fa";
    const intensity = value / max;
    if (intensity >= 0.75) return "#dc3545";
    if (intensity >= 0.5) return "#fd7e14";
    if (intensity >= 0.25) return "#ffc107";
    return "#cfe2ff";
  }

  function getTextColor(value) {
    const intensity = value / max;
    return intensity >= 0.25 ? "#fff" : "#6c757d";
  }

  return (
    <div style={{ overflowX: "auto" }}>
      <table
        style={{ borderCollapse: "collapse", width: "100%", fontSize: 11 }}
      >
        <thead>
          <tr>
            <th
              style={{
                padding: "4px 8px",
                color: "#6c757d",
                fontWeight: 500,
                textAlign: "left",
                minWidth: 40,
              }}
            >
              Day
            </th>
            {hours.map((h) => (
              <th
                key={h}
                style={{
                  padding: "4px 4px",
                  color: "#6c757d",
                  fontWeight: 500,
                  textAlign: "center",
                  minWidth: 44,
                }}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, i) => (
            <tr key={i}>
              <td
                style={{
                  padding: "4px 8px",
                  fontWeight: 600,
                  color: "#495057",
                  whiteSpace: "nowrap",
                }}
              >
                {days[i] ?? row.day}
              </td>
              {row.values.map((val, j) => (
                <td key={j} style={{ padding: 2 }}>
                  <div
                    title={`${days[i]} ${hours[j]}: ${val} visits`}
                    style={{
                      background: getColor(val),
                      color: getTextColor(val),
                      borderRadius: 4,
                      width: 40,
                      height: 28,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontWeight: val > 0 ? 600 : 400,
                      fontSize: 10,
                      cursor: val > 0 ? "pointer" : "default",
                      transition: "transform 0.1s",
                    }}
                    onMouseEnter={(e) =>
                      (e.currentTarget.style.transform = "scale(1.15)")
                    }
                    onMouseLeave={(e) =>
                      (e.currentTarget.style.transform = "scale(1)")
                    }
                  >
                    {val > 0 ? val : ""}
                  </div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>

      {/* Legend */}
      <div className="d-flex align-items-center gap-2 mt-3 flex-wrap">
        <span className="text-muted small me-1">Less</span>
        {["#cfe2ff", "#ffc107", "#fd7e14", "#dc3545"].map((color, i) => (
          <div
            key={i}
            style={{
              width: 18,
              height: 18,
              borderRadius: 3,
              background: color,
            }}
          ></div>
        ))}
        <span className="text-muted small ms-1">More</span>
      </div>
    </div>
  );
}

// ── Monthly Visitors Bar Chart ────────────────────────────────
function MonthlyVisitorsChart({ data }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !data?.length) return;
    if (chartRef.current) chartRef.current.destroy();

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels: data.map((d) => d.month),
        datasets: [
          {
            label: "Total Visits",
            data: data.map((d) => d.count),
            backgroundColor: data.map((_, i) =>
              i === data.length - 1
                ? "rgba(13,110,253,0.9)"
                : "rgba(13,110,253,0.45)",
            ),
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
            callbacks: {
              label: (ctx) => ` ${ctx.parsed.y} visits`,
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: { font: { size: 11 } },
          },
          y: {
            beginAtZero: true,
            ticks: { font: { size: 11 } },
            grid: { color: "rgba(0,0,0,0.05)" },
          },
        },
      },
    });
    return () => chartRef.current?.destroy();
  }, [data]);

  if (!data?.length)
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  return (
    <div
      className="chart-container"
      style={{ height: "220px", position: "relative" }}
    >
      <canvas ref={canvasRef}></canvas>
    </div>
  );
}

// ── Main Dashboard Page ──────────────────────────────────────
export default function Dashboard() {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(false);
  const [selectedFilter, setSelectedFilter] = React.useState("today");
  const latestRequestRef = React.useRef(0);
  const hasLoadedDataRef = React.useRef(false);

  React.useEffect(() => {
    hasLoadedDataRef.current = Boolean(data);
  }, [data]);

  async function loadDashboardData({ silent = false, filterKey = selectedFilter } = {}) {
    const requestId = latestRequestRef.current + 1;
    latestRequestRef.current = requestId;

    if (!silent) {
      setLoading(true);
    }

    try {
      const query = new URLSearchParams({ filter: filterKey }).toString();
      const resp = await fetchJson(`/api/dashboard?${query}`);
      if (requestId !== latestRequestRef.current) return;
      setData(resp);
      setError(false);
    } catch {
      if (requestId !== latestRequestRef.current) return;
      if (!hasLoadedDataRef.current) {
        setError(true);
      }
    } finally {
      if (requestId !== latestRequestRef.current) return;
      if (!silent) {
        setLoading(false);
      }
    }
  }

  React.useEffect(() => {
    loadDashboardData({ filterKey: selectedFilter });

    const timer = window.setInterval(() => {
      loadDashboardData({ silent: true, filterKey: selectedFilter });
    }, 30000);

    return () => window.clearInterval(timer);
  }, [selectedFilter]);

  React.useEffect(() => {
    function handleAnalyticsUpdated() {
      loadDashboardData({ silent: true, filterKey: selectedFilter });
    }

    socket.on("analytics_updated", handleAnalyticsUpdated);
    return () => socket.off("analytics_updated", handleAnalyticsUpdated);
  }, [selectedFilter]);

  if (loading) {
    return (
      <div
        className="d-flex justify-content-center align-items-center"
        style={{ minHeight: "40vh" }}
      >
        <div className="spinner-border text-primary" role="status">
          <span className="visually-hidden">Loading...</span>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="alert alert-danger m-4">
        <i className="bi bi-exclamation-triangle me-2"></i>
        Failed to load dashboard data. Please refresh the page.
      </div>
    );
  }

  const totalLogs = data?.total_logs ?? 0;
  const uniqueVisitors = data?.unique_visitors ?? 0;
  const avgConfidence = data?.avg_confidence ?? 0;
  const totalStudents = data?.total_students ?? 0;
  const dailyVisitors = data?.daily_visitors ?? [];
  const programDistrib = data?.program_distribution ?? [];
  const peakHours = data?.peak_hours ?? [];
  const topVisitors = data?.top_visitors ?? [];
  const weeklyHeatmap = data?.weekly_heatmap ?? [];
  const monthlyVisitors = data?.monthly_visitors ?? [];
  
  // Get filter label from selected filter, not from API response
  const selectedFilterOption = DASHBOARD_FILTER_OPTIONS.find(
    (opt) => opt.value === selectedFilter
  );
  const filterLabel = selectedFilterOption?.label ?? "Last 14 Days";
  const filterDateRange = formatRangeLabel(
    data?.filter_start_date,
    data?.filter_end_date
  );

  // Peak hour label for summary
  const peakHourIdx = peakHours.indexOf(Math.max(...peakHours));
  const peakHourLabel =
    peakHourIdx >= 0
      ? peakHourIdx === 0
        ? "12 AM"
        : peakHourIdx < 12
          ? `${peakHourIdx} AM`
          : peakHourIdx === 12
            ? "12 PM"
            : `${peakHourIdx - 12} PM`
      : "N/A";

  function handleExportClick() {
    if (!data) return;
    downloadDashboardExport(data);
  }

  return (
    <section className="section dashboard">
      <div className="pagetitle d-flex flex-column flex-lg-row justify-content-between align-items-lg-center gap-3">
        <div>
          <h1 className="mb-1">Dashboard</h1>
          <div className="text-muted small">
            Showing {filterLabel}
            {filterDateRange ? ` (${filterDateRange})` : ""}
          </div>
        </div>
        <div className="d-flex flex-column flex-sm-row gap-2 align-items-stretch align-items-sm-center">
          <select
            className="form-select form-select-sm"
            value={selectedFilter}
            onChange={(event) => setSelectedFilter(event.target.value)}
            aria-label="Dashboard filter"
          >
            {DASHBOARD_FILTER_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn-primary btn-sm"
            onClick={handleExportClick}
            disabled={!data}
            style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: '0.4rem', whiteSpace: 'nowrap' }}
          >
            <i className="bi bi-download" style={{ fontSize: '0.9rem' }}></i>
            Export
          </button>
        </div>
      </div>

      {/* ── Stat Cards ── */}
      <div className="row g-3">
        <StatCard
          title="Registered Students"
          value={totalStudents}
          subtext="registered in system"
          iconClass="bi bi-people"
          cardClass="customers-card"
        />
        <StatCard
          title="Recognition Logs"
          value={totalLogs}
          subtext={filterLabel.toLowerCase()}
          iconClass="bi bi-journal-text"
          cardClass="sales-card"
        />
        <StatCard
          title="Unique Visitors"
          value={uniqueVisitors}
          subtext={filterLabel.toLowerCase()}
          iconClass="bi bi-calendar-check"
          cardClass="revenue-card"
        />
        <StatCard
          title="Avg. Confidence"
          value={`${avgConfidence}%`}
          subtext={`within ${filterLabel.toLowerCase()}`}
          iconClass="bi bi-speedometer2"
          cardClass="customers-card"
        />
      </div>

      {/* ── Daily Visitors + Program Distribution ── */}
      <div className="row g-3 mb-3">
        <div className="col-lg-8">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="card-title mb-0">
                  Daily Visitors
                </h5>
                <span className="badge bg-primary-subtle text-primary">
                  <i className="bi bi-graph-up me-1"></i>
                  {filterLabel}
                </span>
              </div>
              <div className="text-muted small mb-2">{filterDateRange}</div>
              <DailyVisitorsChart data={dailyVisitors} />
            </div>
          </div>
        </div>
        <div className="col-lg-4">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="card-title mb-0">Program Distribution</h5>
                <span className="text-muted small">Unique visitors</span>
              </div>
              <ProgramDistributionChart data={programDistrib} />
            </div>
          </div>
        </div>
      </div>

      {/* ── Weekly Heatmap + Monthly Comparison ── */}
      <div className="row g-3 mb-3">
        <div className="col-lg-8">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="card-title mb-0">Weekly Visit Heatmap</h5>
                <span className="text-muted small">
                  <i className="bi bi-calendar3 me-1"></i>
                  {filterLabel}
                </span>
              </div>
              <WeeklyHeatmap data={weeklyHeatmap} />
            </div>
          </div>
        </div>
        <div className="col-lg-4">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="card-title mb-0">Monthly Visitors</h5>
                <span className="badge bg-primary-subtle text-primary">
                  {filterLabel}
                </span>
              </div>
              <MonthlyVisitorsChart data={monthlyVisitors} />
              {monthlyVisitors.length >= 2 &&
                (() => {
                  const last =
                    monthlyVisitors[monthlyVisitors.length - 1]?.count ?? 0;
                  const prev =
                    monthlyVisitors[monthlyVisitors.length - 2]?.count ?? 0;
                  const diff =
                    prev > 0 ? Math.round(((last - prev) / prev) * 100) : 0;
                  const up = diff >= 0;
                  return (
                    <p className="text-muted small mt-2 mb-0">
                      <i
                        className={`bi bi-arrow-${up ? "up" : "down"}-circle-fill text-${up ? "success" : "danger"} me-1`}
                      ></i>
                      {Math.abs(diff)}% {up ? "more" : "fewer"} visits vs last
                      month
                    </p>
                  );
                })()}
            </div>
          </div>
        </div>
      </div>

      {/* ── Peak Hours + Top Visitors ── */}
      <div className="row g-3 mb-3">
        <div className="col-lg-7">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="card-title mb-0">Peak Hours</h5>
                {peakHours.some((v) => v > 0) && (
                  <span className="text-muted small">
                    <i className="bi bi-clock me-1"></i>
                    Busiest at <strong>{peakHourLabel}</strong>
                  </span>
                )}
              </div>
              <div className="d-flex gap-3 mb-3">
                <span className="small">
                  <span
                    className="d-inline-block me-1 rounded"
                    style={{
                      width: 10,
                      height: 10,
                      background: "rgba(220,53,69,0.85)",
                    }}
                  ></span>
                  High
                </span>
                <span className="small">
                  <span
                    className="d-inline-block me-1 rounded"
                    style={{
                      width: 10,
                      height: 10,
                      background: "rgba(255,193,7,0.85)",
                    }}
                  ></span>
                  Medium
                </span>
                <span className="small">
                  <span
                    className="d-inline-block me-1 rounded"
                    style={{
                      width: 10,
                      height: 10,
                      background: "rgba(13,110,253,0.7)",
                    }}
                  ></span>
                  Low
                </span>
              </div>
              <PeakHoursChart data={peakHours} />
            </div>
          </div>
        </div>
        <div className="col-lg-5">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="card-title mb-0">Top Frequent Visitors</h5>
                <span className="badge bg-danger-subtle text-danger">
                  Top {Math.min(topVisitors.length, 10)} in {filterLabel}
                </span>
              </div>
              <TopVisitorsTable data={topVisitors} />
            </div>
          </div>
        </div>
      </div>

      {/* ── Note about entry-only logs ── */}
      <div className="alert alert-info d-flex align-items-center gap-2 py-2">
        <i className="bi bi-info-circle-fill"></i>
        <span className="small">
          <strong>Note:</strong> This system currently records{" "}
          <strong>entry logs only</strong>. Exit tracking is not used in the
          current setup.
        </span>
      </div>
    </section>
  );
}

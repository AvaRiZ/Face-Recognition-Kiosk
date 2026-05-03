import React from "react";
import { getErrorMessage, showError, showSuccess } from "../alerts.js";
import { fetchJson } from "../api.js";
import { socket } from "../socket.js";

const PEAK_HOUR_START = 7;
const PEAK_HOUR_END = 19;
const PEAK_HOUR_COUNT = PEAK_HOUR_END - PEAK_HOUR_START + 1;
const DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function toNonNegativeNumber(value) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return 0;
  }
  return parsed;
}

function normalizeCountList(raw, expectedLength) {
  const normalized = Array.from({ length: expectedLength }, () => 0);
  if (!Array.isArray(raw)) {
    return normalized;
  }
  const limit = Math.min(raw.length, expectedLength);
  for (let i = 0; i < limit; i += 1) {
    normalized[i] = toNonNegativeNumber(raw[i]);
  }
  return normalized;
}

function normalizePeakHours(rawData) {
  if (!Array.isArray(rawData)) {
    return normalizeCountList([], PEAK_HOUR_COUNT);
  }
  if (rawData.length >= 24) {
    return normalizeCountList(
      rawData.slice(PEAK_HOUR_START, PEAK_HOUR_END + 1),
      PEAK_HOUR_COUNT,
    );
  }
  return normalizeCountList(rawData, PEAK_HOUR_COUNT);
}

function formatHourLabel(hour) {
  if (hour === 0) return "12 AM";
  if (hour < 12) return `${hour} AM`;
  if (hour === 12) return "12 PM";
  return `${hour - 12} PM`;
}

const PEAK_HOUR_LABELS = Array.from(
  { length: PEAK_HOUR_COUNT },
  (_, idx) => formatHourLabel(PEAK_HOUR_START + idx),
);

// Stat Card
const DASHBOARD_FILTER_OPTIONS = [
  { value: "today", label: "Today" },
  { value: "last_7_days", label: "Last 7 Days" },
  { value: "last_14_days", label: "Last 14 Days" },
  { value: "last_30_days", label: "Last 30 Days" },
  { value: "last_90_days", label: "Last 90 Days" },
];

const WEEKDAY_SHORT_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function getDashboardViewMode(filterKey) {
  if (filterKey === "today") return "today";
  if (filterKey === "last_7_days" || filterKey === "last_14_days") return "short";
  return "long";
}

function getDashboardViewLabel(filterKey) {
  const mode = getDashboardViewMode(filterKey);
  if (mode === "today") return "Today Snapshot";
  if (mode === "short") return "Short-Range Trend";
  return "Long-Range Trend";
}

function formatDashboardHourLabel(index, startHour = 7) {
  const hour = startHour + index;
  return hour < 12 ? `${hour} AM` : hour === 12 ? "12 PM" : `${hour - 12} PM`;
}

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

function formatSnapshotTime(timestamp) {
  if (!timestamp) return "-";
  const parsed = new Date(timestamp);
  if (Number.isNaN(parsed.getTime())) return String(timestamp);
  return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function sanitizeWorksheetName(name) {
  return String(name || "Sheet")
    .replace(/[\\/*?:[\]]/g, " ")
    .slice(0, 31);
}

function autosizeColumns(rows = [], minWidth = 12) {
  const columnCount = rows.reduce(
    (max, row) => Math.max(max, Array.isArray(row) ? row.length : 0),
    0
  );

  return Array.from({ length: columnCount }, (_, index) => {
    const width = rows.reduce((max, row) => {
      const value = row?.[index];
      const length = value === null || value === undefined ? 0 : String(value).length;
      return Math.max(max, length);
    }, minWidth);

    return { wch: Math.min(Math.max(width + 2, minWidth), 40) };
  });
}

function buildWorksheet(XLSX, rows, options = {}) {
  const worksheet = XLSX.utils.aoa_to_sheet(rows);
  worksheet["!cols"] = options.columns || autosizeColumns(rows, options.minWidth);
  if (options.merges?.length) {
    worksheet["!merges"] = options.merges;
  }
  if (options.freezeTopRow) {
    worksheet["!freeze"] = { xSplit: 0, ySplit: 1 };
  }
  return worksheet;
}

function buildWeekdayPatternRows(weeklyHeatmap) {
  return WEEKDAY_SHORT_LABELS.map((day, index) => {
    const row = weeklyHeatmap?.[index];
    const total = (row?.values ?? []).reduce((sum, value) => sum + (Number(value) || 0), 0);
    return [day, total];
  });
}

function buildHeatmapSheetRows(weeklyHeatmap = []) {
  const hourHeaders = Array.from({ length: 13 }, (_, index) =>
    formatDashboardHourLabel(index)
  );

  return [
    ["Day", ...hourHeaders],
    ...WEEKDAY_SHORT_LABELS.map((day, index) => [
      day,
      ...Array.from({ length: 13 }, (_, hourIndex) => weeklyHeatmap?.[index]?.values?.[hourIndex] ?? 0),
    ]),
  ];
}

function buildDashboardWorkbook(XLSX, data, filterKey = data?.filter_key) {
  const viewMode = getDashboardViewMode(filterKey);
  const workbook = XLSX.utils.book_new();
  const filterLabel =
    data?.filter_label ??
    DASHBOARD_FILTER_OPTIONS.find((item) => item.value === filterKey)?.label ??
    "";
  const dateRangeLabel = formatRangeLabel(
    data?.filter_start_date,
    data?.filter_end_date
  );

  const summaryRows = [
    ["Dashboard Export"],
    [],
    ["Filter", filterLabel],
    ["Dashboard View", getDashboardViewLabel(filterKey)],
    ["Date Range", dateRangeLabel],
    [],
    ["Summary"],
    ["Metric", "Value"],
    ["Registered Students", data?.total_students ?? 0],
    ["Recognition Logs", data?.total_logs ?? 0],
    ["Unique Visitors", data?.unique_visitors ?? 0],
    ["Average Confidence", `${data?.avg_confidence ?? 0}%`],
  ];

  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, summaryRows, {
      minWidth: 18,
      merges: [{ s: { r: 0, c: 0 }, e: { r: 0, c: 1 } }],
    }),
    "Summary"
  );

  const dailyVisitorRows = [
    ["Date", "Visits"],
    ...(data?.daily_visitors ?? []).map((item) => [item.date, item.count]),
  ];
  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, dailyVisitorRows, { minWidth: 14, freezeTopRow: true }),
    sanitizeWorksheetName("Daily Visitors")
  );

  const programRows = [
    ["Program", "Unique Visitors"],
    ...(data?.program_distribution ?? []).map((item) => [item.program || "Unknown", item.count]),
  ];
  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, programRows, { minWidth: 16, freezeTopRow: true }),
    sanitizeWorksheetName("Program Distribution")
  );

  const hourRows = [
    ["Hour", "Visits"],
    ...(data?.peak_hours ?? []).map((count, hourIndex) => [
      formatDashboardHourLabel(hourIndex),
      count,
    ]),
  ];
  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, hourRows, { minWidth: 14, freezeTopRow: true }),
    sanitizeWorksheetName(viewMode === "today" ? "Hourly Visitors" : "Peak Hours")
  );

  const topVisitorRows = [
    ["Name", "SR Code", "Visits"],
    ...(data?.top_visitors ?? []).map((item) => [item.name, item.sr_code, item.visits]),
  ];
  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, topVisitorRows, { minWidth: 14, freezeTopRow: true }),
    sanitizeWorksheetName("Top Visitors")
  );

  if (viewMode !== "today") {
    XLSX.utils.book_append_sheet(
      workbook,
      buildWorksheet(XLSX, buildHeatmapSheetRows(data?.weekly_heatmap ?? []), {
        minWidth: 10,
        freezeTopRow: true,
      }),
      sanitizeWorksheetName("Weekly Heatmap")
    );
  }

  if (viewMode === "short") {
    const weekdayRows = [
      ["Weekday", "Visits"],
      ...buildWeekdayPatternRows(data?.weekly_heatmap ?? []),
    ];
    XLSX.utils.book_append_sheet(
      workbook,
      buildWorksheet(XLSX, weekdayRows, { minWidth: 14, freezeTopRow: true }),
      sanitizeWorksheetName("Weekday Pattern")
    );
  }

  if (viewMode === "long") {
    const monthlyRows = [
      ["Month", "Visits"],
      ...(data?.monthly_visitors ?? []).map((item) => [item.month, item.count]),
    ];
    XLSX.utils.book_append_sheet(
      workbook,
      buildWorksheet(XLSX, monthlyRows, { minWidth: 14, freezeTopRow: true }),
      sanitizeWorksheetName("Monthly Visitors")
    );
  }

  return workbook;
}

async function downloadDashboardExport(data, filterKey) {
  const XLSX = await import("xlsx");
  const workbook = buildDashboardWorkbook(XLSX, data, filterKey);
  XLSX.writeFile(
    workbook,
    `dashboard-${filterKey ?? data?.filter_key ?? "export"}-${new Date().toISOString().slice(0, 10)}.xlsx`
  );
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

// Daily Visitors Line Chart
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

  if (!Array.isArray(data) || !data.length)
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

// Program Distribution Pie Chart
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

  if (!Array.isArray(data) || !data.length)
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

// Peak Hours Heatmap
function PeakHoursChart({ data }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const normalizedData = React.useMemo(() => normalizePeakHours(data), [data]);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !normalizedData.length) return;
    if (chartRef.current) chartRef.current.destroy();

    const maxVisits = Math.max(...normalizedData, 0);

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels: PEAK_HOUR_LABELS,
        datasets: [
          {
            label: "Visits",
            data: normalizedData,
            backgroundColor: normalizedData.map((visits) => {
              const intensity = maxVisits ? visits / maxVisits : 0;
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
              source: "labels", // force using all labels
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
  }, [normalizedData]);

  if (!Array.isArray(data) || !data.length)
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

// Top Frequent Visitors Table
function TopVisitorsTable({ data }) {
  if (!data?.length) {
    return (
      <div className="text-center text-muted py-4">
        <i className="bi bi-people fs-3 d-block mb-2"></i>
        No visitor data yet.
      </div>
    );
  }

  const normalizedData = data.map((visitor) => ({
    ...visitor,
    name: visitor?.name || "Unknown",
    sr_code: visitor?.sr_code || "N/A",
    visits: toNonNegativeNumber(visitor?.visits),
  }));
  const max = Math.max(...normalizedData.map((visitor) => visitor.visits), 1);

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
          {normalizedData.map((visitor, i) => (
            <tr key={`${visitor.sr_code}-${i}`}>
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

// Weekly Heatmap
function WeeklyHeatmap({ data }) {
  if (!data?.length) {
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  }

  const hours = PEAK_HOUR_LABELS.map((label) => label.replace(" ", ""));
  const normalizedRows = data.map((row, index) => {
    const day =
      typeof row?.day === "string" && row.day
        ? row.day
        : DAYS_OF_WEEK[index] || `Day ${index + 1}`;
    return {
      day,
      values: normalizeCountList(row?.values, PEAK_HOUR_COUNT),
    };
  });

  // Find max value for color scaling
  const allValues = normalizedRows.flatMap((row) => row.values);
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
          {normalizedRows.map((row, i) => (
            <tr key={`${row.day}-${i}`}>
              <td
                style={{
                  padding: "4px 8px",
                  fontWeight: 600,
                  color: "#495057",
                  whiteSpace: "nowrap",
                }}
              >
                {row.day}
              </td>
              {row.values.map((val, j) => (
                <td key={j} style={{ padding: 2 }}>
                  <div
                    title={`${row.day} ${hours[j]}: ${val} visits`}
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

// Monthly Visitors Bar Chart
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

function WeekdayPatternChart({ data }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !data?.length) return;
    if (chartRef.current) chartRef.current.destroy();

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels: data.map((item) => item.label),
        datasets: [
          {
            label: "Visits",
            data: data.map((item) => item.count),
            backgroundColor: data.map((_, index) =>
              index === data.length - 1
                ? "rgba(25,135,84,0.9)"
                : "rgba(25,135,84,0.45)"
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

// ── Main Dashboard Page ──────────────────────────────────────
export default function Dashboard() {
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [selectedFilter, setSelectedFilter] = React.useState("today");
  const [occupancyData, setOccupancyData] = React.useState(null);
  const [occupancyHistory, setOccupancyHistory] = React.useState([]);
  const [activeAlerts, setActiveAlerts] = React.useState([]);
  const [occupancyPanelError, setOccupancyPanelError] = React.useState("");
  const [overrideAdjustment, setOverrideAdjustment] = React.useState("");
  const [overrideReason, setOverrideReason] = React.useState("");
  const [overrideSubmitting, setOverrideSubmitting] = React.useState(false);
  const [dismissInFlightId, setDismissInFlightId] = React.useState(null);
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

  async function loadOccupancyPanel({ silent = false } = {}) {
    try {
      const todayIso = new Date().toISOString().slice(0, 10);
      const [currentResp, historyResp, alertsResp] = await Promise.all([
        fetchJson("/api/occupancy/current"),
        fetchJson(`/api/occupancy/history?date=${todayIso}&limit=24`),
        fetchJson("/api/alerts?active=true&limit=20"),
      ]);
      setOccupancyData(currentResp || null);
      setOccupancyHistory(
        Array.isArray(historyResp?.snapshots)
          ? [...historyResp.snapshots].reverse()
          : [],
      );
      setActiveAlerts(Array.isArray(alertsResp?.alerts) ? alertsResp.alerts : []);
      setOccupancyPanelError("");
    } catch (err) {
      if (!silent) {
        setOccupancyPanelError(
          getErrorMessage(err, "Failed to load occupancy and alert data."),
        );
      }
    }
  }

  React.useEffect(() => {
    loadDashboardData({ filterKey: selectedFilter });
    loadOccupancyPanel();

    const timer = window.setInterval(() => {
      loadDashboardData({ silent: true, filterKey: selectedFilter });
      loadOccupancyPanel({ silent: true });
    }, 30000);

    return () => window.clearInterval(timer);
  }, [selectedFilter]);

  React.useEffect(() => {
    function handleAnalyticsUpdated() {
      loadDashboardData({ silent: true, filterKey: selectedFilter });
      loadOccupancyPanel({ silent: true });
    }

    function handleCapacityAlert() {
      loadOccupancyPanel({ silent: true });
    }

    socket.connect();
    socket.on("analytics_updated", handleAnalyticsUpdated);
    socket.on("capacity_threshold_alert", handleCapacityAlert);
    return () => {
      socket.off("analytics_updated", handleAnalyticsUpdated);
      socket.off("capacity_threshold_alert", handleCapacityAlert);
      socket.disconnect();
    };
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
  const peakHoursRaw = data?.peak_hours ?? [];
  const peakHours = normalizePeakHours(peakHoursRaw);
  const topVisitors = data?.top_visitors ?? [];
  const weeklyHeatmap = data?.weekly_heatmap ?? [];
  const monthlyVisitors = data?.monthly_visitors ?? [];
  const viewMode = getDashboardViewMode(selectedFilter);
  const isTodayView = viewMode === "today";
  const isShortRangeView = viewMode === "short";
  const weekdayPattern = buildWeekdayPatternRows(weeklyHeatmap).map(
    ([label, count]) => ({ label, count })
  );
  
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
  const peakHourMax = Math.max(...peakHours, 0);
  const peakHourIdx = peakHourMax > 0 ? peakHours.indexOf(peakHourMax) : -1;
  const peakHourLabel =
    peakHourIdx >= 0 ? formatDashboardHourLabel(peakHourIdx) : "N/A";

  async function handleExportClick() {
    if (!data) return;
    setExporting(true);
    try {
      await downloadDashboardExport(data, selectedFilter);
      await showSuccess(
        "Export Complete",
        `Dashboard data for ${filterLabel} was exported to Excel successfully.`
      );
    } catch (exportError) {
      await showError(
        "Export Failed",
        getErrorMessage(exportError, "The Excel file could not be generated.")
      );
    } finally {
      setExporting(false);
    }
  }

  async function handleDismissAlert(alertId) {
    if (!alertId || dismissInFlightId !== null) return;
    setDismissInFlightId(alertId);
    try {
      await fetchJson(`/api/alerts/${alertId}/dismiss`, { method: "POST" });
      await showSuccess("Alert Acknowledged", "Capacity alert has been dismissed.");
      await loadOccupancyPanel({ silent: true });
    } catch (err) {
      await showError("Dismiss Failed", getErrorMessage(err, "Unable to dismiss alert."));
    } finally {
      setDismissInFlightId(null);
    }
  }

  async function handleManualOverrideSubmit(event) {
    event.preventDefault();
    const adjustment = Number.parseInt(String(overrideAdjustment).trim(), 10);
    const reason = String(overrideReason || "").trim();

    if (!Number.isInteger(adjustment) || adjustment === 0) {
      await showError("Invalid Adjustment", "Enter a non-zero integer adjustment value.");
      return;
    }
    if (!reason) {
      await showError("Missing Reason", "Provide a reason for the manual occupancy override.");
      return;
    }

    setOverrideSubmitting(true);
    try {
      await fetchJson("/api/occupancy/adjust", {
        method: "POST",
        body: JSON.stringify({ adjustment, reason }),
      });
      setOverrideAdjustment("");
      setOverrideReason("");
      await showSuccess("Override Applied", "Occupancy state was adjusted successfully.");
      await Promise.all([
        loadOccupancyPanel({ silent: true }),
        loadDashboardData({ silent: true, filterKey: selectedFilter }),
      ]);
    } catch (err) {
      await showError("Override Failed", getErrorMessage(err, "Unable to apply manual adjustment."));
    } finally {
      setOverrideSubmitting(false);
    }
  }

  const capacityLimit = Number(occupancyData?.capacity_limit ?? data?.max_occupancy ?? 0);
  const occupancyCount = Number(occupancyData?.occupancy_count ?? data?.current_occupancy ?? 0);
  const dailyEntries = Number(occupancyData?.daily_entries ?? 0);
  const dailyExits = Number(occupancyData?.daily_exits ?? 0);
  const isFull = Boolean(occupancyData?.is_full);
  const capacityWarning = Boolean(occupancyData?.capacity_warning);
  const occupancyRatioRaw =
    Number(occupancyData?.occupancy_ratio) ||
    (capacityLimit > 0 ? occupancyCount / capacityLimit : 0);
  const occupancyRatio = Number.isFinite(occupancyRatioRaw) ? occupancyRatioRaw : 0;
  const occupancyPercent = Math.max(0, Math.min(100, Math.round(occupancyRatio * 100)));
  const occupancyStatusLabel = isFull
    ? "Full Capacity"
    : capacityWarning
      ? "Warning Threshold"
      : "Normal Capacity";
  const occupancyStatusClass = isFull
    ? "bg-danger"
    : capacityWarning
      ? "bg-warning text-dark"
      : "bg-success";

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
            disabled={!data || exporting}
            style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: '0.4rem', whiteSpace: 'nowrap' }}
          >
            <i className="bi bi-download" style={{ fontSize: '0.9rem' }}></i>
            {exporting ? "Exporting..." : "Export"}
          </button>
        </div>
      </div>

      {/* Stat Cards */}
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

      {occupancyPanelError ? (
        <div className="alert alert-danger mt-3 mb-3">
          <i className="bi bi-exclamation-triangle me-2"></i>
          {occupancyPanelError}
        </div>
      ) : null}

      <div className="row g-3 mb-3">
        <div className="col-xl-8">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-2">
                <h5 className="card-title mb-0">Live Occupancy Monitor</h5>
                <span className={`badge ${occupancyStatusClass}`}>{occupancyStatusLabel}</span>
              </div>
              <div className="d-flex flex-wrap align-items-end gap-3 mb-2">
                <div>
                  <div className="display-6 fw-bold mb-0">
                    {occupancyCount}
                    <span className="text-muted fs-5">/{capacityLimit || 0}</span>
                  </div>
                  <div className="text-muted small">Current occupancy vs configured capacity</div>
                </div>
                <div className="ms-auto text-end">
                  <div className="fw-semibold">{occupancyPercent}% utilized</div>
                  <div className="text-muted small">
                    Entries today: <strong>{dailyEntries}</strong> · Exits today: <strong>{dailyExits}</strong>
                  </div>
                </div>
              </div>
              <div className="progress mb-3" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={occupancyPercent}>
                <div
                  className={`progress-bar ${isFull ? "bg-danger" : capacityWarning ? "bg-warning" : "bg-success"}`}
                  style={{ width: `${occupancyPercent}%` }}
                />
              </div>
              <div className="d-flex justify-content-between align-items-center mb-2">
                <h6 className="mb-0">Recent Occupancy Snapshots</h6>
                <span className="text-muted small">Today</span>
              </div>
              {occupancyHistory.length ? (
                <div className="table-responsive">
                  <table className="table table-sm align-middle mb-0">
                    <thead>
                      <tr>
                        <th>Time</th>
                        <th>Occupancy</th>
                        <th>Entries</th>
                        <th>Exits</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {occupancyHistory.slice(-8).map((snapshot) => (
                        <tr key={`${snapshot.snapshot_timestamp}-${snapshot.occupancy_count}`}>
                          <td>{formatSnapshotTime(snapshot.snapshot_timestamp)}</td>
                          <td>{snapshot.occupancy_count}/{snapshot.capacity_limit}</td>
                          <td>{snapshot.daily_entries}</td>
                          <td>{snapshot.daily_exits}</td>
                          <td>
                            {snapshot.capacity_warning ? (
                              <span className="badge bg-warning text-dark">Warning</span>
                            ) : (
                              <span className="badge bg-success">Normal</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-muted small">No snapshots available yet for today.</div>
              )}
            </div>
          </div>
        </div>
        <div className="col-xl-4 d-flex flex-column gap-3">
          <div className="card">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-2">
                <h5 className="card-title mb-0">Capacity Alerts</h5>
                <span className="badge bg-secondary">{activeAlerts.length}</span>
              </div>
              {activeAlerts.length ? (
                <div className="d-flex flex-column gap-2">
                  {activeAlerts.slice(0, 5).map((alert) => (
                    <div key={alert.id} className="border rounded p-2">
                      <div className="d-flex justify-content-between align-items-start gap-2">
                        <div>
                          <div className="fw-semibold small">{alert.message || "Capacity alert"}</div>
                          <div className="text-muted small">
                            {alert.occupancy_count}/{alert.capacity_limit} · {Math.round((alert.occupancy_ratio || 0) * 100)}%
                          </div>
                          <div className="text-muted small">
                            {formatSnapshotTime(alert.created_at)} · {String(alert.level || "").toUpperCase()}
                          </div>
                        </div>
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-secondary"
                          onClick={() => handleDismissAlert(alert.id)}
                          disabled={dismissInFlightId === alert.id}
                        >
                          {dismissInFlightId === alert.id ? "..." : "Acknowledge"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-muted small">No active capacity alerts.</div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-body">
              <h5 className="card-title mb-2">Manual Occupancy Override</h5>
              <p className="text-muted small mb-3">
                Apply a signed adjustment to reconcile occupancy drift.
              </p>
              <form onSubmit={handleManualOverrideSubmit}>
                <div className="mb-2">
                  <label className="form-label small mb-1" htmlFor="occupancy-adjustment">Adjustment</label>
                  <input
                    id="occupancy-adjustment"
                    type="number"
                    className="form-control form-control-sm"
                    placeholder="e.g. +2 or -1"
                    value={overrideAdjustment}
                    onChange={(event) => setOverrideAdjustment(event.target.value)}
                    disabled={overrideSubmitting}
                  />
                </div>
                <div className="mb-2">
                  <label className="form-label small mb-1" htmlFor="occupancy-reason">Reason</label>
                  <textarea
                    id="occupancy-reason"
                    className="form-control form-control-sm"
                    rows={2}
                    placeholder="Reason for this correction"
                    value={overrideReason}
                    onChange={(event) => setOverrideReason(event.target.value)}
                    disabled={overrideSubmitting}
                  />
                </div>
                <button type="submit" className="btn btn-sm btn-primary" disabled={overrideSubmitting}>
                  {overrideSubmitting ? "Applying..." : "Apply Override"}
                </button>
              </form>
            </div>
          </div>
        </div>
      </div>

      {/* Daily Visitors + Program Distribution */}
      <div className="row g-3 mb-3">
        <div className="col-lg-8">
          <div className="card h-100">
            <div className="card-body">
              <div className="d-flex justify-content-between align-items-center mb-3">
                <h5 className="card-title mb-0">
                  {isTodayView ? "Hourly Visitors Today" : "Daily Visitors"}
                </h5>
                <span className="badge bg-primary-subtle text-primary">
                  <i className="bi bi-graph-up me-1"></i>
                  {filterLabel}
                </span>
              </div>
              <div className="text-muted small mb-2">
                {isTodayView
                  ? "A one-day filter is shown by hour so the activity pattern is easier to read."
                  : filterDateRange}
              </div>
              {isTodayView ? (
                <PeakHoursChart data={peakHours} />
              ) : (
                <DailyVisitorsChart data={dailyVisitors} />
              )}
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

      {/* Weekly Heatmap + Monthly Comparison */}
      <div className="row g-3 mb-3">
        <div className="col-lg-8">
          <div className="card h-100">
            <div className="card-body">
              {isTodayView ? (
                <>
                  <div className="d-flex justify-content-between align-items-center mb-3">
                    <h5 className="card-title mb-0">Top Frequent Visitors</h5>
                    <span className="badge bg-danger-subtle text-danger">
                      Top {Math.min(topVisitors.length, 10)} in {filterLabel}
                    </span>
                  </div>
                  <TopVisitorsTable data={topVisitors} />
                </>
              ) : (
                <>
                  <div className="d-flex justify-content-between align-items-center mb-3">
                    <h5 className="card-title mb-0">Weekly Visit Heatmap</h5>
                    <span className="text-muted small">
                      <i className="bi bi-calendar3 me-1"></i>
                      {filterLabel}
                    </span>
                  </div>
                  <WeeklyHeatmap data={weeklyHeatmap} />
                </>
              )}
            </div>
          </div>
        </div>
        <div className="col-lg-4">
          <div className="card h-100">
            <div className="card-body">
              {isTodayView ? (
                <>
                  <div className="d-flex justify-content-between align-items-center mb-3">
                    <h5 className="card-title mb-0">Range-Based Views</h5>
                    <span className="badge bg-secondary-subtle text-secondary">
                      Context
                    </span>
                  </div>
                  <div className="text-muted small mb-3">
                    Today stays focused on same-day movement. Switch to longer
                    filters to reveal comparative views like weekly heatmaps and
                    monthly trend context.
                  </div>
                  <div className="border rounded-3 p-3 bg-light">
                    <div className="fw-semibold small mb-2">
                      Best fit per filter
                    </div>
                    <div className="text-muted small">Today: hourly activity</div>
                    <div className="text-muted small">7-14 days: weekday pattern</div>
                    <div className="text-muted small">30-90 days: monthly context</div>
                  </div>
                </>
              ) : isShortRangeView ? (
                <>
                  <div className="d-flex justify-content-between align-items-center mb-3">
                    <h5 className="card-title mb-0">Weekday Pattern</h5>
                    <span className="badge bg-success-subtle text-success">
                      Within selected filter
                    </span>
                  </div>
                  <WeekdayPatternChart data={weekdayPattern} />
                  <p className="text-muted small mt-2 mb-0">
                    Short-range filters highlight recurring weekday attendance
                    instead of forcing a monthly comparison.
                  </p>
                </>
              ) : (
                <>
                  <div className="d-flex justify-content-between align-items-center mb-3">
                    <h5 className="card-title mb-0">Monthly Visitors</h5>
                    <span className="badge bg-primary-subtle text-primary">
                      Context chart
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
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Peak Hours + Top Visitors ── */}
      {!isTodayView ? (
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
              <PeakHoursChart data={peakHoursRaw} />
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
      ) : null}

      {/* Dual-camera occupancy note */}
      <div className="alert alert-info d-flex align-items-center gap-2 py-2">
        <i className="bi bi-info-circle-fill"></i>
        <span className="small">
          <strong>Dual-camera mode:</strong> Camera 1 records entries and Camera 2 records exits.
          Occupancy, alerts, and manual overrides update both flows in real time.
        </span>
      </div>
    </section>
  );
}


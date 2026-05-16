import React from "react";
import { createPortal } from "react-dom";
import html2canvas from "html2canvas";
import { jsPDF } from "jspdf";
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
const ANALYTICS_CACHE_KEY = "analytics-executive-cache-v2";
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

// ── Export Helpers ─────────────────────────────────────────────
function formatExportDate(value = new Date()) {
  const source = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(source.getTime())) return "";
  return source.toISOString().slice(0, 10);
}

function sanitizeExportText(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/\s+/g, " ").trim();
}

function csvCell(value) {
  const text = sanitizeExportText(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function addCsvSection(rows, title, headers, items, mapper) {
  rows.push([title]);
  if (headers?.length) rows.push(headers);
  const sourceItems = Array.isArray(items) ? items : [];
  if (sourceItems.length) {
    sourceItems.forEach((item, index) => rows.push(mapper(item, index)));
  } else {
    rows.push(["No data"]);
  }
  rows.push([]);
}

function downloadTextFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

function getExportGenderData(data) {
  return (data?.gender_data || [])
    .map((item) => ({
      gender: item?.gender || item?.label || "Unknown",
      count: Number(item?.count || 0),
    }))
    .filter((item) => {
      const normalized = String(item.gender || "").trim().toLowerCase();
      return item.count > 0 && !["", "unknown", "n/a", "na", "-"].includes(normalized);
    });
}

function getExportYearData(data) {
  return (data?.year_level_data || [])
    .map((item) => ({
      year_level: item?.year_level || item?.label || "Unknown",
      count: Number(item?.count || 0),
    }))
    .filter((item) => item.count > 0);
}

const EXPORT_BRANDING_DEFAULTS = {
  systemName: "Automated Facial Recognition Library Logging System",
  institutionName: "Batangas State University",
  libraryName: "University Library",
  reportType: "Executive Institutional Analytics Report",
  generatedBy: "Analytics Reporting Engine",
  confidentiality: "Confidential - Internal Use Only",
};

function toNumber(value) {
  const normalized = Number(value);
  return Number.isFinite(normalized) ? normalized : 0;
}

function formatPercent(value, decimals = 1) {
  return `${toNumber(value).toFixed(decimals)}%`;
}

function formatSignedPercent(value, decimals = 1) {
  const normalized = toNumber(value);
  const prefix = normalized > 0 ? "+" : "";
  return `${prefix}${normalized.toFixed(decimals)}%`;
}

function getTrendDeltaPercent(counts = []) {
  if (!Array.isArray(counts) || counts.length < 2) return 0;
  const windowSize = Math.min(7, counts.length);
  const firstWindow = counts.slice(0, windowSize).map((value) => toNumber(value));
  const lastWindow = counts.slice(-windowSize).map((value) => toNumber(value));
  const firstAvg = firstWindow.reduce((sum, value) => sum + value, 0) / Math.max(firstWindow.length, 1);
  const lastAvg = lastWindow.reduce((sum, value) => sum + value, 0) / Math.max(lastWindow.length, 1);
  if (firstAvg <= 0) return 0;
  return ((lastAvg - firstAvg) / firstAvg) * 100;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function buildReportingPeriodLabel(referenceDate = new Date()) {
  const end = referenceDate instanceof Date ? referenceDate : new Date(referenceDate);
  const start = new Date(end);
  start.setDate(end.getDate() - 29);
  return `${start.toLocaleDateString()} - ${end.toLocaleDateString()}`;
}

function buildExecutiveInsights({
  trendDirection,
  trendDeltaPct,
  peakHour,
  peakDow,
  topProgram,
  topYearLevel,
  recognitionAccuracy,
  reliabilityScore,
  forecastPeakDay,
}) {
  const trendStatement =
    trendDirection === "upward"
      ? `Library traffic increased by ${Math.abs(trendDeltaPct).toFixed(1)}% compared with the opening week of the current 30-day period.`
      : trendDirection === "downward"
        ? `Library traffic declined by ${Math.abs(trendDeltaPct).toFixed(1)}% relative to the opening week of the current 30-day period.`
        : "Library traffic is stable with no material increase or decrease in the current 30-day period.";

  const reliabilityStatement =
    recognitionAccuracy >= 90 && reliabilityScore >= 90
      ? "Recognition reliability remains within strong operational thresholds for institutional reporting."
      : recognitionAccuracy >= 80 && reliabilityScore >= 80
        ? "Recognition reliability remains within acceptable operational thresholds, with minor optimization opportunities."
        : "Recognition reliability is below preferred thresholds and should be reviewed for camera positioning and capture quality.";

  return [
    trendStatement,
    `Peak utilization occurs around ${peakHour.label}, with ${peakDow.label} showing the highest average day-level demand.`,
    `${topProgram.label} is the most active program cohort in the reporting window.`,
    topYearLevel
      ? `${topYearLevel.year_level} students currently represent the highest recorded year-level participation.`
      : "Year-level participation data is currently limited and should be monitored as additional records are collected.",
    reliabilityStatement,
    forecastPeakDay
      ? `Forecasted demand peaks on ${forecastPeakDay}, supporting proactive staffing and circulation planning.`
      : "Forecasting remains limited; continue collecting records to improve predictive confidence.",
  ];
}

function buildAnalyticsReportPayload(data, reportContext = {}, exportedAt = new Date()) {
  const dq = data?.data_quality || {};
  const stats = data?.descriptive_stats || {};
  const realtime = data?.realtime_status || {};
  const forecast = data?.forecast || {};
  const forecastValues = reportContext?.forecastValues || forecast?.values || [];
  const forecastLabels = reportContext?.forecastLabels || forecast?.labels || [];
  const last30Counts = reportContext?.last30Counts || data?.last_30_counts || [];
  const last30Labels = reportContext?.last30Labels || data?.last_30_labels || [];
  const peakHours = reportContext?.peakHours || normalizePeakHours(data?.peak_hours || []);
  const peakDow = reportContext?.peakDow || getPeakDow(data?.dow_labels || [], data?.dow_averages || []);
  const peakHour = reportContext?.peakHour || getPeakHour(peakHours);
  const programDistribution = reportContext?.programDistribution || data?.program_distribution || [];
  const topProgram = reportContext?.topProgram || getTopProgram(programDistribution);
  const genderData = reportContext?.genderData || getExportGenderData(data);
  const yearLevelData = reportContext?.yearLevelData || normalizeYearLevelData(data?.year_level_data || []);
  const topYearLevel = yearLevelData?.length
    ? [...yearLevelData].sort((a, b) => toNumber(b.count) - toNumber(a.count))[0]
    : null;
  const trendDirection = reportContext?.trendDirection || getTrendDirection(last30Counts);
  const trendDeltaPct = reportContext?.trendDeltaPct ?? getTrendDeltaPercent(last30Counts);
  const trendLabel =
    reportContext?.trendLabel
    || (trendDirection === "upward" ? "Increasing" : trendDirection === "downward" ? "Declining" : "Stable");

  const confidenceEligibleLive = toNumber(dq?.total_live_confidence_eligible);
  const recognitionAccuracy = reportContext?.recognitionAccuracy ?? (
    confidenceEligibleLive > 0
      ? Math.max(0, Math.min(100, toNumber(dq?.avg_live_confidence)))
      : toNumber(realtime?.avg_confidence)
  );
  const reliabilityScore = reportContext?.reliabilityScore ?? toNumber(dq?.quality_score);

  const currentOccupancy = reportContext?.currentOccupancy ?? toNumber(realtime?.current_occupancy);
  const maxOccupancy = reportContext?.maxOccupancy ?? toNumber(realtime?.max_occupancy);
  const occupancyPct = reportContext?.occupancyPct ?? (maxOccupancy > 0 ? (currentOccupancy / maxOccupancy) * 100 : 0);
  const occupancyStatus = reportContext?.occupancyStatus
    || realtime?.occupancy_status
    || (occupancyPct >= 90 ? "Approaching capacity" : occupancyPct >= 70 ? "Moderately busy" : "Available");

  const entriesToday = reportContext?.entriesToday ?? toNumber(realtime?.total_entries ?? realtime?.today_logs);
  const exitsToday = reportContext?.exitsToday ?? toNumber(realtime?.total_exits);
  const totalVisits = reportContext?.totalVisits ?? toNumber(data?.total_cleaned_logs ?? dq?.total_cleaned);
  const avgDailyVisitors = reportContext?.avgDailyVisitors ?? (
    toNumber(stats?.mean_daily_visits) > 0
      ? toNumber(stats?.mean_daily_visits)
      : (last30Counts.length
        ? (last30Counts.reduce((sum, value) => sum + toNumber(value), 0) / last30Counts.length)
        : 0)
  );

  const forecastTotalWeek = reportContext?.forecastTotalWeek ?? forecastValues.reduce((sum, value) => sum + toNumber(value), 0);
  const forecastPeakIndex = forecastValues.length ? forecastValues.indexOf(Math.max(...forecastValues)) : -1;
  const forecastPeakDay = reportContext?.forecastPeakDay
    || (forecastPeakIndex >= 0 ? forecastLabels[forecastPeakIndex] : "");
  const forecastModel = reportContext?.forecastModel || data?.best_forecast_model || forecast?.model || "Not available";

  const topPrograms = programDistribution.slice(0, 3).map((entry) => ({
    label: normalizeProgramLabel(entry?.program || entry?.label || "Unknown"),
    count: toNumber(entry?.count),
  }));
  const mostActiveProgramsLabel = topPrograms.length
    ? topPrograms.map((item) => `${item.label} (${fmt(item.count)})`).join(", ")
    : "No dominant program recorded";

  const trendSummary =
    trendDirection === "upward"
      ? `Growth trend (${formatSignedPercent(trendDeltaPct)})`
      : trendDirection === "downward"
        ? `Decline trend (${formatSignedPercent(trendDeltaPct)})`
        : "Stable trend";

  const branding = {
    ...EXPORT_BRANDING_DEFAULTS,
    ...(reportContext?.branding || {}),
  };

  const insights = (reportContext?.keyInsights?.length
    ? reportContext.keyInsights
    : buildExecutiveInsights({
        trendDirection,
        trendDeltaPct,
        peakHour,
        peakDow,
        topProgram,
        topYearLevel,
        recognitionAccuracy,
        reliabilityScore,
        forecastPeakDay,
      })
  ).map((line) => sanitizeExportText(line)).filter(Boolean);

  const recommendations = (reportContext?.recommendations || [])
    .map((line) => sanitizeExportText(line))
    .filter(Boolean);

  return {
    exportedAt,
    branding: {
      ...branding,
      reportingPeriod: reportContext?.branding?.reportingPeriod || buildReportingPeriodLabel(exportedAt),
    },
    metrics: {
      totalVisits,
      avgDailyVisitors,
      currentOccupancy,
      maxOccupancy,
      occupancyPct,
      occupancyStatus,
      peakOperatingHours: peakHour.label,
      peakDow: peakDow.label,
      mostActiveProgramsLabel,
      recognitionAccuracy,
      reliabilityScore,
      forecastTotalWeek,
      forecastPeakDay,
      forecastModel,
      trendLabel,
      trendDirection,
      trendDeltaPct,
      trendSummary,
      entriesToday,
      exitsToday,
    },
    raw: {
      dq,
      stats,
      realtime,
      forecast,
      last30Labels,
      last30Counts,
      peakHours,
      programDistribution,
      genderData,
      yearLevelData,
      topPrograms,
      topProgram,
      topYearLevel,
      forecastLabels,
      forecastValues,
      confidenceEligibleLive,
      forecastComparison: data?.forecast_comparison || [],
      anomalies: data?.anomalies || [],
      segmentation: data?.segmentation || null,
    },
    insights,
    recommendations,
    chartSections: (reportContext?.charts || []).filter(
      (item) => Array.isArray(item?.series) && item.series.length > 0,
    ),
    analyticsMode: data?.analytics_mode || "",
    generatedAtLabel: exportedAt.toLocaleString(),
  };
}

function buildAnalyticsExportRows(report) {
  const rows = [];
  const { branding, metrics, raw } = report;
  const forecastRows = raw.forecastLabels.map((label, index) => ({
    label,
    value: raw.forecastValues[index] ?? 0,
    lower: raw.forecast?.lower?.[index] ?? "",
    upper: raw.forecast?.upper?.[index] ?? "",
  }));

  addCsvSection(rows, "Report Metadata", ["Field", "Value"], [
    ["System Name", branding.systemName],
    ["Institution", branding.institutionName],
    ["Library", branding.libraryName],
    ["Report Type", branding.reportType],
    ["Reporting Period", branding.reportingPeriod],
    ["Generated At", report.generatedAtLabel],
    ["Generated By", branding.generatedBy],
    ["Confidentiality", branding.confidentiality],
    ["Analytics Mode", report.analyticsMode || "full"],
  ], (item) => item);

  addCsvSection(rows, "1. Executive Summary", ["KPI", "Value", "Institutional Context"], [
    ["Total Library Visits", fmt(metrics.totalVisits), "Validated cleaned visit records"],
    ["Average Daily Visitors", metrics.avgDailyVisitors.toFixed(1), "Mean attendance per day"],
    ["Current Occupancy", `${fmt(metrics.currentOccupancy)} of ${fmt(metrics.maxOccupancy)}`, `${metrics.occupancyPct.toFixed(1)}% utilized`],
    ["Peak Operating Hours", metrics.peakOperatingHours, `Highest activity day: ${metrics.peakDow}`],
    ["Most Active Programs", metrics.mostActiveProgramsLabel, "Top represented cohorts"],
    ["Recognition Accuracy Rate", formatPercent(metrics.recognitionAccuracy), "Live confidence-derived accuracy"],
    ["Data Quality Score", formatPercent(metrics.reliabilityScore), "Data reliability and cleaning yield"],
    ["Forecasted Weekly Traffic", fmt(metrics.forecastTotalWeek), `Projected peak day: ${metrics.forecastPeakDay || "N/A"}`],
    ["Attendance Trend", metrics.trendSummary, "Comparative first-week vs last-week average"],
  ], (item) => item);

  addCsvSection(rows, "Executive Insights", ["#", "Interpretation"], report.insights.map((line, index) => ({
    index: index + 1,
    line,
  })), (item) => [item.index, item.line]);

  addCsvSection(rows, "2. Real-Time Library Status", ["Metric", "Value"], [
    ["Current Occupancy", metrics.currentOccupancy],
    ["Occupancy Capacity", metrics.maxOccupancy],
    ["Occupancy Utilization", formatPercent(metrics.occupancyPct)],
    ["Occupancy Status", metrics.occupancyStatus],
    ["Entries Today", metrics.entriesToday],
    ["Exits Today", metrics.exitsToday],
    ["Recognition Accuracy", formatPercent(metrics.recognitionAccuracy)],
  ], (item) => item);

  const last30Rows = raw.last30Labels.map((label, index) => ({
    label,
    count: raw.last30Counts[index] ?? 0,
  }));
  addCsvSection(rows, "3. Attendance & Usage Trends", ["Date", "Visits"], last30Rows, (item) => [item.label, item.count]);
  rows.push(["Total 30-Day Visits", last30Rows.reduce((sum, item) => sum + toNumber(item.count), 0)]);
  rows.push([]);

  const peakHourRows = raw.peakHours.map((entry) => ({
    hour: formatHourLabel(entry.hour),
    count: toNumber(entry.count),
  }));
  addCsvSection(rows, "Peak Hour Distribution", ["Hour", "Visits"], peakHourRows, (item) => [item.hour, item.count]);

  const programRows = raw.programDistribution.map((entry) => ({
    label: normalizeProgramLabel(entry?.program || entry?.label || "Unknown"),
    count: toNumber(entry?.count),
  }));
  addCsvSection(rows, "4. Student Engagement Analytics - Program Distribution", ["Program", "Visits"], programRows, (item) => [item.label, item.count]);
  rows.push(["Total Program-Mapped Visits", programRows.reduce((sum, item) => sum + toNumber(item.count), 0)]);
  rows.push([]);

  addCsvSection(rows, "Student Engagement - Gender Composition", ["Gender", "Count"], raw.genderData, (item) => [item.gender, item.count]);
  rows.push(["Total Gender-Profiled Records", raw.genderData.reduce((sum, item) => sum + toNumber(item.count), 0)]);
  rows.push([]);
  addCsvSection(rows, "Student Engagement - Year Level Participation", ["Year Level", "Count"], raw.yearLevelData, (item) => [item.year_level, item.count]);
  rows.push(["Total Year-Level-Profiled Records", raw.yearLevelData.reduce((sum, item) => sum + toNumber(item.count), 0)]);
  rows.push([]);

  addCsvSection(rows, "5. Forecast & Predictive Insights", ["Date", "Predicted Visits", "Lower Bound", "Upper Bound"], forecastRows, (item) => [item.label, item.value, item.lower, item.upper]);
  rows.push(["Projected 7-Day Total", metrics.forecastTotalWeek, "", ""]);
  rows.push(["Forecast Model", metrics.forecastModel, "", ""]);
  rows.push([]);

  addCsvSection(rows, "Forecast Model Benchmark", ["Model", "MAE", "RMSE", "MAPE", "7-Day Total"], raw.forecastComparison || [], (item) => [
    item?.model || "",
    item?.mae ?? "",
    item?.rmse ?? "",
    item?.mape ?? "",
    item?.total_7d ?? "",
  ]);

  addCsvSection(rows, "6. Operational Performance Metrics", ["Measure", "Value"], [
    ["Occupancy Utilization", formatPercent(metrics.occupancyPct)],
    ["Peak Operating Day", metrics.peakDow],
    ["Peak Operating Hour", metrics.peakOperatingHours],
    ["Entries vs Exits (Today)", `${fmt(metrics.entriesToday)} / ${fmt(metrics.exitsToday)}`],
    ["Attendance Trend Direction", metrics.trendLabel],
    ["Attendance Trend Delta", formatSignedPercent(metrics.trendDeltaPct)],
  ], (item) => item);

  addCsvSection(rows, "7. Data Quality & Recognition Reliability", ["Metric", "Value"], [
    ["Raw Records", raw.dq?.total_raw ?? 0],
    ["Live Raw Records", raw.dq?.total_live ?? 0],
    ["Imported Raw Records", raw.dq?.total_imported ?? 0],
    ["Cleaned Records", raw.dq?.total_cleaned ?? 0],
    ["Low Confidence Removed", raw.dq?.removed_low_conf ?? 0],
    ["Outside-Hours Removed", raw.dq?.removed_outside_hrs ?? 0],
    ["Duplicate Removed", raw.dq?.removed_duplicates ?? 0],
    ["Zero Confidence Ignored", raw.dq?.excluded_zero_conf ?? 0],
    ["Recognition Accuracy", formatPercent(metrics.recognitionAccuracy)],
    ["Data Reliability Score", formatPercent(metrics.reliabilityScore)],
  ], (item) => item);

  addCsvSection(rows, "8. Detailed Statistical Appendix", ["Statistic", "Value"], [
    ["Mean Daily Visits", raw.stats?.mean_daily_visits ?? 0],
    ["Median Daily Visits", raw.stats?.median_daily_visits ?? 0],
    ["Max Daily Visits", raw.stats?.max_daily_visits ?? 0],
    ["Min Daily Visits", raw.stats?.min_daily_visits ?? 0],
    ["Standard Deviation", raw.stats?.std_dev ?? 0],
    ["Active Visit Days", raw.stats?.total_visit_days ?? 0],
    ["Anomaly Flags", raw.anomalies?.length ?? 0],
    ["Regular Users", raw.segmentation?.regular_count ?? ""],
    ["Occasional Users", raw.segmentation?.occasional_count ?? ""],
    ["Rare Users", raw.segmentation?.rare_count ?? ""],
  ], (item) => item);

  return rows;
}

function downloadAnalyticsCsv(data, reportContext = {}) {
  const exportedAt = new Date();
  const report = buildAnalyticsReportPayload(data, reportContext, exportedAt);
  const rows = buildAnalyticsExportRows(report);
  const csv = rows.map((row) => row.map(csvCell).join(",")).join("\r\n");
  downloadTextFile(`analytics-reports-${formatExportDate(exportedAt)}.csv`, csv, "text/csv;charset=utf-8");
}

function buildWorksheetColumns(rows = [], minWidth = 12) {
  const columnCount = rows.reduce(
    (max, row) => Math.max(max, Array.isArray(row) ? row.length : 0),
    0,
  );

  return Array.from({ length: columnCount }, (_, index) => {
    const width = rows.reduce((max, row) => {
      const value = row?.[index];
      const length = value === null || value === undefined ? 0 : String(value).length;
      return Math.max(max, length);
    }, minWidth);

    return { wch: Math.min(Math.max(width + 2, minWidth), 48) };
  });
}

async function downloadAnalyticsExcel(data, reportContext = {}) {
  const exportedAt = new Date();
  const report = buildAnalyticsReportPayload(data, reportContext, exportedAt);
  const rows = buildAnalyticsExportRows(report);
  const XLSX = await import("xlsx");
  const workbook = XLSX.utils.book_new();
  const worksheet = XLSX.utils.aoa_to_sheet(rows);
  worksheet["!cols"] = buildWorksheetColumns(rows, 13);
  XLSX.utils.book_append_sheet(workbook, worksheet, "Analytics Report");
  XLSX.writeFile(workbook, `analytics-reports-${formatExportDate(exportedAt)}.xlsx`);
}

async function buildSupplementalChartSections() {
  const endDate = new Date();
  const startDate = new Date(endDate);
  startDate.setDate(endDate.getDate() - 13);
  const startIso = startDate.toISOString().slice(0, 10);
  const endIso = endDate.toISOString().slice(0, 10);

  const [occupancyResult, eventsResult] = await Promise.allSettled([
    fetchJson("/api/analytics/occupancy-trends?days=14"),
    fetchJson(`/api/events?start_date=${startIso}&end_date=${endIso}`),
  ]);

  const charts = [];

  if (occupancyResult.status === "fulfilled") {
    const points = occupancyResult.value?.data || [];
    if (points.length) {
      const labels = points.map((item) => item?.date?.slice(5) || "");
      const avgSeries = points.map((item) => toNumber(item?.avg_occupancy));
      const peakSeries = points.map((item) => toNumber(item?.peak_occupancy));
      const peakValue = Math.max(...peakSeries, 0);
      charts.push({
        id: "occupancy-trend",
        title: "Occupancy Trends",
        subtitle: "Daily average and peak occupancy (last 14 days)",
        options: {
          chart: { type: "line" },
          colors: ["#1d4ed8", "#0f766e"],
          xaxis: { categories: labels },
          yaxis: { title: { text: "Occupancy" } },
        },
        series: [
          { name: "Average Occupancy", data: avgSeries },
          { name: "Peak Occupancy", data: peakSeries },
        ],
        interpretation: `Peak observed occupancy reached ${fmt(peakValue)} during the 14-day monitoring window.`,
      });
    }
  }

  if (eventsResult.status === "fulfilled") {
    const rows = eventsResult.value?.rows || [];
    const confidenceByDay = new Map();
    rows.forEach((row) => {
      const day = row?.date || row?.timestamp?.slice(0, 10);
      const conf = toNumber(row?.conf_pct);
      if (!day || conf <= 0) return;
      const aggregate = confidenceByDay.get(day) || { sum: 0, count: 0 };
      aggregate.sum += conf;
      aggregate.count += 1;
      confidenceByDay.set(day, aggregate);
    });

    if (confidenceByDay.size > 0) {
      const labels = [];
      const values = [];
      for (let offset = 0; offset < 14; offset += 1) {
        const day = new Date(startDate);
        day.setDate(startDate.getDate() + offset);
        const dayKey = day.toISOString().slice(0, 10);
        labels.push(dayKey.slice(5));
        const aggregate = confidenceByDay.get(dayKey);
        values.push(
          aggregate && aggregate.count > 0
            ? Number((aggregate.sum / aggregate.count).toFixed(1))
            : null,
        );
      }

      const filtered = values.filter((value) => value !== null);
      const average = filtered.length
        ? filtered.reduce((sum, value) => sum + toNumber(value), 0) / filtered.length
        : 0;

      charts.push({
        id: "recognition-accuracy-trend",
        title: "Recognition Accuracy Trends",
        subtitle: "Daily average recognition confidence (last 14 days)",
        options: {
          chart: { type: "line" },
          colors: ["#d97706"],
          markers: { size: 3 },
          xaxis: { categories: labels },
          yaxis: { title: { text: "Accuracy %" } },
        },
        series: [
          { name: "Average Confidence", data: values },
        ],
        interpretation: `Recognition confidence averaged ${average.toFixed(1)}% in the monitored two-week period.`,
      });
    }
  }

  return charts;
}

async function renderChartImageForPdf(section, width = 1200, height = 520) {
  if (!section || !window.Chart || !Array.isArray(section.series) || section.series.length === 0) {
    return null;
  }

  const host = document.createElement("div");
  host.style.position = "fixed";
  host.style.left = "-20000px";
  host.style.top = "0";
  host.style.width = `${width}px`;
  host.style.height = `${height}px`;
  host.style.padding = "20px";
  host.style.background = "#ffffff";
  host.style.boxSizing = "border-box";
  host.style.zIndex = "-1";

  const canvas = document.createElement("canvas");
  canvas.style.width = `${width - 40}px`;
  canvas.style.height = `${height - 40}px`;
  canvas.width = (width - 40) * 2;
  canvas.height = (height - 40) * 2;
  host.appendChild(canvas);
  document.body.appendChild(host);

  let chart = null;
  try {
    const config = buildChartJsConfigForPdf({
      options: section.options || {},
      series: section.series || [],
    });

    config.options = {
      ...config.options,
      responsive: false,
      maintainAspectRatio: false,
      animation: false,
      devicePixelRatio: 2,
    };

    chart = new window.Chart(canvas, config);
    chart.update("none");
    await new Promise((resolve) => window.setTimeout(resolve, 50));
    const imageDataUrl = canvas.toDataURL("image/png", 1);
    return {
      ...section,
      imageDataUrl,
    };
  } catch (error) {
    console.error("Failed to prepare chart image for export:", section?.title, error);
    return null;
  } finally {
    chart?.destroy();
    host.remove();
  }
}

function renderChartCard(chart) {
  if (!chart) return "";
  return `
    <div class="chart-card">
      <div class="chart-card-title">${escapeHtml(chart.title || "Chart")}</div>
      ${chart.subtitle ? `<div class="chart-card-subtitle">${escapeHtml(chart.subtitle)}</div>` : ""}
      ${
        chart.imageDataUrl
          ? `<img class="chart-image" src="${chart.imageDataUrl}" alt="${escapeHtml(chart.title || "chart")}" />`
          : `<div class="chart-empty">Chart data unavailable for this reporting window.</div>`
      }
      ${chart.interpretation ? `<div class="chart-note">${escapeHtml(chart.interpretation)}</div>` : ""}
    </div>
  `;
}

function renderSimpleTable(headers, rows) {
  const headHtml = `<tr>${headers.map((header) => `<th>${escapeHtml(header)}</th>`).join("")}</tr>`;
  const rowHtml = rows.map((row) => `<tr>${row.map((cell) => `<td>${escapeHtml(cell)}</td>`).join("")}</tr>`).join("");
  return `
    <table class="report-table">
      <thead>${headHtml}</thead>
      <tbody>${rowHtml}</tbody>
    </table>
  `;
}

function buildPdfMarkup(report, chartCards = []) {
  const { branding, metrics, raw, insights, recommendations, generatedAtLabel } = report;
  const chartById = new Map(chartCards.map((item) => [item.id, item]));
  const attendanceCharts = ["daily-attendance", "weekday-usage", "peak-hours"]
    .map((id) => chartById.get(id))
    .filter(Boolean);
  const engagementCharts = ["program-distribution", "year-level-participation", "gender-composition"]
    .map((id) => chartById.get(id))
    .filter(Boolean);
  const forecastCharts = ["forecast-traffic"]
    .map((id) => chartById.get(id))
    .filter(Boolean);
  const realtimeCharts = ["occupancy-trend", "recognition-accuracy-trend"]
    .map((id) => chartById.get(id))
    .filter(Boolean);

  const topProgramsRows = raw.topPrograms.length
    ? raw.topPrograms.map((item) => [item.label, fmt(item.count)])
    : [["No dominant program detected", "0"]];
  const dataQualityRows = [
    ["Raw records", fmt(raw.dq?.total_raw || 0)],
    ["Cleaned records", fmt(raw.dq?.total_cleaned || 0)],
    ["Low confidence removed", fmt(raw.dq?.removed_low_conf || 0)],
    ["Outside-hours removed", fmt(raw.dq?.removed_outside_hrs || 0)],
    ["Duplicates removed", fmt(raw.dq?.removed_duplicates || 0)],
    ["Recognition accuracy", formatPercent(metrics.recognitionAccuracy)],
    ["Data quality score", formatPercent(metrics.reliabilityScore)],
  ];
  const appendixRows = [
    ["Mean daily visits", raw.stats?.mean_daily_visits ?? 0],
    ["Median daily visits", raw.stats?.median_daily_visits ?? 0],
    ["Maximum daily visits", raw.stats?.max_daily_visits ?? 0],
    ["Minimum daily visits", raw.stats?.min_daily_visits ?? 0],
    ["Standard deviation", raw.stats?.std_dev ?? 0],
    ["Total active days", raw.stats?.total_visit_days ?? 0],
    ["Anomaly flags", raw.anomalies?.length ?? 0],
    ["Forecast model", metrics.forecastModel],
    ["Forecast peak day", metrics.forecastPeakDay || "N/A"],
  ];

  const pageFooter = `
    <div class="page-footer">
      <span>${escapeHtml(branding.systemName)}</span>
      <span>${escapeHtml(branding.confidentiality)}</span>
    </div>
  `;

  return `
    <style>
      .analytics-export-root {
        width: 794px;
        background: #dfe7f2;
        font-family: "Segoe UI", Arial, sans-serif;
        color: #0f172a;
      }
      .analytics-export-page {
        width: 794px;
        min-height: 1123px;
        background: #ffffff;
        box-sizing: border-box;
        padding: 40px 36px 36px;
        position: relative;
        display: flex;
        flex-direction: column;
        gap: 14px;
      }
      .cover-panel {
        background: linear-gradient(135deg, #0f172a 0%, #1d4ed8 54%, #0f766e 100%);
        border-radius: 16px;
        padding: 26px;
        color: #ffffff;
      }
      .cover-title {
        font-size: 32px;
        font-weight: 800;
        line-height: 1.08;
        margin-bottom: 10px;
      }
      .cover-subtitle {
        font-size: 15px;
        opacity: 0.9;
        line-height: 1.6;
      }
      .meta-grid {
        margin-top: 12px;
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 10px;
      }
      .meta-item {
        background: #f8fafc;
        border: 1px solid #dbe2ef;
        border-radius: 10px;
        padding: 12px 14px;
      }
      .meta-label {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #64748b;
        font-weight: 700;
      }
      .meta-value {
        margin-top: 6px;
        font-size: 14px;
        font-weight: 700;
        color: #0f172a;
      }
      .section-title {
        font-size: 15px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: #475569;
        font-weight: 800;
        margin-top: 4px;
      }
      .kpi-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      .kpi-card {
        border: 1px solid #dbe2ef;
        border-radius: 10px;
        background: #f8fafc;
        padding: 12px 13px;
      }
      .kpi-label {
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        color: #64748b;
        font-weight: 700;
      }
      .kpi-value {
        margin-top: 5px;
        font-size: 20px;
        font-weight: 800;
        color: #0f172a;
        line-height: 1.15;
      }
      .kpi-note {
        margin-top: 5px;
        font-size: 12.5px;
        color: #475569;
        line-height: 1.4;
      }
      .callout-list {
        display: grid;
        gap: 9px;
      }
      .callout-item {
        border: 1px solid #dbe2ef;
        border-left: 4px solid #1d4ed8;
        border-radius: 8px;
        background: #ffffff;
        padding: 10px 12px;
        font-size: 13.5px;
        line-height: 1.6;
        color: #1e293b;
      }
      .mini-grid {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
      }
      .mini-card {
        border: 1px solid #dbe2ef;
        border-radius: 10px;
        padding: 12px;
        background: #ffffff;
      }
      .mini-label {
        font-size: 11px;
        color: #64748b;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-weight: 700;
      }
      .mini-value {
        margin-top: 5px;
        font-size: 19px;
        font-weight: 800;
        color: #0f172a;
      }
      .chart-grid {
        display: grid;
        grid-template-columns: 1fr;
        gap: 12px;
      }
      .chart-card {
        border: 1px solid #dbe2ef;
        border-radius: 12px;
        background: #ffffff;
        padding: 14px 16px;
      }
      .chart-card-title {
        font-size: 15.5px;
        color: #0f172a;
        font-weight: 800;
      }
      .chart-card-subtitle {
        margin-top: 4px;
        font-size: 12.5px;
        color: #64748b;
      }
      .chart-image {
        width: 100%;
        margin-top: 10px;
        border-radius: 8px;
        border: 1px solid #e2e8f0;
        background: #ffffff;
      }
      .chart-empty {
        margin-top: 10px;
        border: 1px dashed #cbd5e1;
        border-radius: 8px;
        background: #f8fafc;
        color: #64748b;
        font-size: 12px;
        padding: 18px;
        text-align: center;
      }
      .chart-note {
        margin-top: 10px;
        border-top: 1px solid #e2e8f0;
        padding-top: 8px;
        font-size: 13px;
        line-height: 1.6;
        color: #334155;
      }
      .report-table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
      }
      .report-table th,
      .report-table td {
        border: 1px solid #dbe2ef;
        padding: 8px 9px;
        text-align: left;
      }
      .report-table th {
        background: #f1f5f9;
        color: #334155;
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-weight: 700;
      }
      .report-table td {
        color: #0f172a;
      }
      .page-footer {
        position: absolute;
        left: 36px;
        right: 36px;
        bottom: 20px;
        border-top: 1px solid #dbe2ef;
        padding-top: 8px;
        display: flex;
        justify-content: space-between;
        color: #64748b;
        font-size: 10.5px;
      }
    </style>
    <div class="analytics-export-root">
      <div class="analytics-export-page">
        <div class="cover-panel">
          <div class="cover-title">${escapeHtml(branding.reportType)}</div>
          <div class="cover-subtitle">
            Institutional analytics report for administrators, executive leadership, and accreditation stakeholders.
          </div>
        </div>
        <div class="meta-grid">
          <div class="meta-item"><div class="meta-label">System</div><div class="meta-value">${escapeHtml(branding.systemName)}</div></div>
          <div class="meta-item"><div class="meta-label">Institution</div><div class="meta-value">${escapeHtml(branding.institutionName)}</div></div>
          <div class="meta-item"><div class="meta-label">Library</div><div class="meta-value">${escapeHtml(branding.libraryName)}</div></div>
          <div class="meta-item"><div class="meta-label">Reporting Period</div><div class="meta-value">${escapeHtml(branding.reportingPeriod)}</div></div>
          <div class="meta-item"><div class="meta-label">Generated At</div><div class="meta-value">${escapeHtml(generatedAtLabel)}</div></div>
          <div class="meta-item"><div class="meta-label">Generated By</div><div class="meta-value">${escapeHtml(branding.generatedBy)}</div></div>
        </div>
        <div class="section-title">1. Executive Summary</div>
        <div class="kpi-grid">
          <div class="kpi-card"><div class="kpi-label">Total Library Visits</div><div class="kpi-value">${fmt(metrics.totalVisits)}</div><div class="kpi-note">Validated cleaned attendance records</div></div>
          <div class="kpi-card"><div class="kpi-label">Average Daily Visitors</div><div class="kpi-value">${metrics.avgDailyVisitors.toFixed(1)}</div><div class="kpi-note">Mean daily attendance volume</div></div>
          <div class="kpi-card"><div class="kpi-label">Current Occupancy</div><div class="kpi-value">${fmt(metrics.currentOccupancy)}</div><div class="kpi-note">of ${fmt(metrics.maxOccupancy)} capacity (${metrics.occupancyPct.toFixed(1)}%)</div></div>
          <div class="kpi-card"><div class="kpi-label">Peak Operating Hours</div><div class="kpi-value">${escapeHtml(metrics.peakOperatingHours)}</div><div class="kpi-note">Highest demand day: ${escapeHtml(metrics.peakDow)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Most Active Programs</div><div class="kpi-value">${escapeHtml(raw.topPrograms[0]?.label || "N/A")}</div><div class="kpi-note">${escapeHtml(metrics.mostActiveProgramsLabel)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Recognition Accuracy</div><div class="kpi-value">${formatPercent(metrics.recognitionAccuracy)}</div><div class="kpi-note">Operational confidence indicator</div></div>
          <div class="kpi-card"><div class="kpi-label">Data Quality Score</div><div class="kpi-value">${formatPercent(metrics.reliabilityScore)}</div><div class="kpi-note">Post-cleaning data reliability</div></div>
          <div class="kpi-card"><div class="kpi-label">Forecasted Weekly Traffic</div><div class="kpi-value">${fmt(metrics.forecastTotalWeek)}</div><div class="kpi-note">Projected peak day: ${escapeHtml(metrics.forecastPeakDay || "N/A")}</div></div>
          <div class="kpi-card"><div class="kpi-label">Attendance Trend</div><div class="kpi-value">${escapeHtml(metrics.trendLabel)}</div><div class="kpi-note">${escapeHtml(metrics.trendSummary)}</div></div>
        </div>
        <div class="section-title">Executive Insights</div>
        <div class="callout-list">
          ${insights.map((line) => `<div class="callout-item">${escapeHtml(line)}</div>`).join("")}
        </div>
        ${pageFooter}
      </div>

      <div class="analytics-export-page">
        <div class="section-title">2. Real-Time Library Status</div>
        <div class="mini-grid">
          <div class="mini-card"><div class="mini-label">Current Occupancy</div><div class="mini-value">${fmt(metrics.currentOccupancy)}</div></div>
          <div class="mini-card"><div class="mini-label">Occupancy Status</div><div class="mini-value">${escapeHtml(metrics.occupancyStatus)}</div></div>
          <div class="mini-card"><div class="mini-label">Entries Today</div><div class="mini-value">${fmt(metrics.entriesToday)}</div></div>
          <div class="mini-card"><div class="mini-label">Exits Today</div><div class="mini-value">${fmt(metrics.exitsToday)}</div></div>
        </div>
        <div class="section-title">Real-Time Performance Visuals</div>
        <div class="chart-grid">
          ${realtimeCharts.map((chart) => renderChartCard(chart)).join("")}
        </div>
        <div class="section-title">Administrative Interpretation</div>
        <div class="callout-list">
          <div class="callout-item">Current occupancy is ${metrics.occupancyPct.toFixed(1)}% of configured capacity, currently categorized as ${escapeHtml(metrics.occupancyStatus.toLowerCase())}.</div>
          <div class="callout-item">Recognition accuracy is ${formatPercent(metrics.recognitionAccuracy)}, supporting ${metrics.recognitionAccuracy >= 85 ? "stable operational monitoring." : "targeted calibration and quality-control improvements."}</div>
        </div>
        ${pageFooter}
      </div>

      <div class="analytics-export-page">
        <div class="section-title">3. Attendance & Usage Trends</div>
        <div class="chart-grid">
          ${attendanceCharts.map((chart) => renderChartCard(chart)).join("")}
        </div>
        <div class="section-title">Attendance Notes</div>
        <div class="callout-list">
          <div class="callout-item">Attendance trend is currently ${escapeHtml(metrics.trendLabel.toLowerCase())}, with a ${formatSignedPercent(metrics.trendDeltaPct)} change between the first and most recent weeks.</div>
          <div class="callout-item">Peak utilization remains anchored around ${escapeHtml(metrics.peakOperatingHours)} and ${escapeHtml(metrics.peakDow)}.</div>
        </div>
        ${pageFooter}
      </div>

      <div class="analytics-export-page">
        <div class="section-title">4. Student Engagement Analytics</div>
        <div class="chart-grid">
          ${engagementCharts.map((chart) => renderChartCard(chart)).join("")}
        </div>
        <div class="section-title">Top Engagement Cohorts</div>
        ${renderSimpleTable(["Program", "Visits"], topProgramsRows)}
        ${pageFooter}
      </div>

      <div class="analytics-export-page">
        <div class="section-title">5. Forecast & Predictive Insights</div>
        <div class="chart-grid">
          ${forecastCharts.map((chart) => renderChartCard(chart)).join("")}
        </div>
        <div class="section-title">Forecast Summary</div>
        <div class="mini-grid">
          <div class="mini-card"><div class="mini-label">Forecasted 7-Day Traffic</div><div class="mini-value">${fmt(metrics.forecastTotalWeek)}</div></div>
          <div class="mini-card"><div class="mini-label">Forecast Peak Day</div><div class="mini-value">${escapeHtml(metrics.forecastPeakDay || "N/A")}</div></div>
          <div class="mini-card"><div class="mini-label">Forecast Model</div><div class="mini-value">${escapeHtml(metrics.forecastModel)}</div></div>
          <div class="mini-card"><div class="mini-label">Trend Baseline</div><div class="mini-value">${escapeHtml(metrics.trendLabel)}</div></div>
        </div>
        <div class="section-title">6. Operational Performance Metrics</div>
        <div class="callout-list">
          ${recommendations.map((line) => `<div class="callout-item">${escapeHtml(line)}</div>`).join("")}
        </div>
        <div class="section-title">7. Data Quality & Recognition Reliability</div>
        ${renderSimpleTable(["Metric", "Value"], dataQualityRows)}
        ${pageFooter}
      </div>

      <div class="analytics-export-page">
        <div class="section-title">8. Detailed Statistical Appendix</div>
        ${renderSimpleTable(["Statistic", "Value"], appendixRows)}
        <div class="section-title">Forecast Model Comparison</div>
        ${
          raw.forecastComparison.length
            ? renderSimpleTable(
                ["Model", "MAE", "RMSE", "MAPE", "7-Day Total"],
                raw.forecastComparison.map((row) => [
                  row?.model || "",
                  row?.mae ?? "",
                  row?.rmse ?? "",
                  row?.mape ?? "",
                  row?.total_7d ?? "",
                ]),
              )
            : `<div class="chart-empty">Forecast model benchmark data is unavailable in the current payload.</div>`
        }
        ${pageFooter}
      </div>
    </div>
  `;
}

async function waitForImages(root) {
  const images = Array.from(root.querySelectorAll("img"));
  if (!images.length) return;
  await Promise.all(images.map((image) => {
    if (image.complete) return Promise.resolve();
    return new Promise((resolve) => {
      image.onload = resolve;
      image.onerror = resolve;
    });
  }));
}

async function openAnalyticsPdf(data, reportContext = {}) {
  const exportedAt = new Date();
  const report = buildAnalyticsReportPayload(data, reportContext, exportedAt);
  const supplementalCharts = await buildSupplementalChartSections();
  const sourceCharts = [...report.chartSections, ...supplementalCharts];
  const chartCards = [];
  for (const section of sourceCharts) {
    const card = await renderChartImageForPdf(section);
    if (card) chartCards.push(card);
  }

  const exportRoot = document.createElement("div");
  exportRoot.style.position = "fixed";
  exportRoot.style.left = "-20000px";
  exportRoot.style.top = "0";
  exportRoot.style.zIndex = "-1";
  exportRoot.innerHTML = buildPdfMarkup(report, chartCards);
  document.body.appendChild(exportRoot);

  try {
    await waitForImages(exportRoot);
    const pages = Array.from(exportRoot.querySelectorAll(".analytics-export-page"));
    if (!pages.length) {
      throw new Error("The PDF report layout did not render correctly.");
    }

    const pdf = new jsPDF({
      orientation: "portrait",
      unit: "mm",
      format: "a4",
      compress: true,
    });

    const pageWidth = 210;
    const pageHeight = 297;

    for (let index = 0; index < pages.length; index += 1) {
      const page = pages[index];
      const canvas = await html2canvas(page, {
        scale: 3,
        useCORS: true,
        backgroundColor: "#ffffff",
      });
      const image = canvas.toDataURL("image/png");

      if (index > 0) pdf.addPage("a4", "portrait");
      pdf.addImage(image, "PNG", 0, 0, pageWidth, pageHeight, undefined, "NONE");
      pdf.setFontSize(8.5);
      pdf.setTextColor(100, 116, 139);
      pdf.text(
        `Page ${index + 1} of ${pages.length}`,
        pageWidth - 10,
        pageHeight - 5,
        { align: "right" },
      );
    }

    pdf.save(`analytics-reports-${formatExportDate(exportedAt)}.pdf`);
  } finally {
    exportRoot.remove();
  }
}

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
function ExportModal({ data, reportContext = {}, disabled = false }) {
  const [showModal, setShowModal] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);

  React.useEffect(() => {
    const h = (e) => {
      if (e.key === "Escape") setShowModal(false);
    };
    if (showModal) document.addEventListener("keydown", h);
    return () => document.removeEventListener("keydown", h);
  }, [showModal]);

  async function handleExport(type) {
    if (!data) return;
    setExporting(true);
    try {
      if (type === "csv") {
        downloadAnalyticsCsv(data, reportContext);
        await showSuccess("Export Complete", "Executive analytics CSV report generated successfully.");
      } else if (type === "excel") {
        await downloadAnalyticsExcel(data, reportContext);
        await showSuccess("Export Complete", "Executive analytics Excel report generated successfully.");
      } else {
        await openAnalyticsPdf(data, reportContext);
        await showSuccess("Export Complete", "Executive analytics PDF report generated successfully.");
      }
      setShowModal(false);
    } catch (error) {
      await showError(
        "Export Failed",
        getErrorMessage(error, "The analytics report export could not be generated."),
      );
    } finally {
      setExporting(false);
    }
  }

  return (
    <>
      <button
        type="button"
        className="btn btn-sm btn-outline-success d-flex align-items-center gap-1 px-2 py-1"
        onClick={() => setShowModal(true)}
        disabled={disabled || !data || exporting}
      >
        <i className="bi bi-upload" style={{ fontSize: 12 }}></i>
        {exporting ? "Exporting..." : "Export"}
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
          }}
          onClick={(e) => e.target === e.currentTarget && setShowModal(false)}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: 16,
              width: "100%",
              maxWidth: 420,
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
                    background: "rgba(25,135,84,0.08)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <i className="bi bi-upload text-success" style={{ fontSize: 17 }}></i>
                </div>
                <div>
                  <div style={{ fontWeight: 700, fontSize: 14, color: "#1a1a2e" }}>
                    Export Analytics Report
                  </div>
                  <div style={{ fontSize: 11, color: "#aaa" }}>
                    Choose CSV, Excel, or PDF
                  </div>
                </div>
              </div>
              <button
                type="button"
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
            <div style={{ padding: 22, display: "grid", gap: 12 }}>
              <button
                type="button"
                className="btn btn-outline-primary d-flex align-items-center justify-content-between"
                onClick={() => handleExport("csv")}
                disabled={exporting}
              >
                <span className="d-flex align-items-center gap-2">
                  <i className="bi bi-filetype-csv"></i>
                  CSV Export
                </span>
                <i className="bi bi-chevron-right"></i>
              </button>
              <button
                type="button"
                className="btn btn-outline-success d-flex align-items-center justify-content-between"
                onClick={() => handleExport("excel")}
                disabled={exporting}
              >
                <span className="d-flex align-items-center gap-2">
                  <i className="bi bi-file-earmark-excel"></i>
                  Excel Export
                </span>
                <i className="bi bi-chevron-right"></i>
              </button>
              <button
                type="button"
                className="btn btn-outline-danger d-flex align-items-center justify-content-between"
                onClick={() => handleExport("pdf")}
                disabled={exporting}
              >
                <span className="d-flex align-items-center gap-2">
                  <i className="bi bi-filetype-pdf"></i>
                  PDF Export
                </span>
                <i className="bi bi-chevron-right"></i>
              </button>
            </div>
          </div>
        </div>
      ), document.body)}
    </>
  );
}

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
        <i className="bi bi-download" style={{ fontSize: 12 }}></i> Import Data
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
                    className="bi bi-cloud-download text-primary"
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
      sub: "Below 50% confidence (live logs only; 0% unmatched ignored)",
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
            optimize staff scheduling across both weekday and weekend service
            periods.
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
        Weekend changes should be interpreted as part of the normal operating
        pattern because Saturday and Sunday are library-open days.{" "}
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
        historical weekend traffic as part of the normal operating pattern.
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
          "No statistically significant anomalies were found. Daily visit counts remain within normal bounds (±2 std dev), including normal weekday and weekend operating patterns."
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
  const normalized = normalizePeakHours(peakHours);
  if (!normalized.length) return { label: "—", value: 0 };
  const peak = [...normalized].sort((a, b) => (b.count || 0) - (a.count || 0))[0];
  return {
    label: formatHourLabel(peak?.hour),
    value: peak?.count || 0,
  };
}

function normalizePeakHours(peakHours = []) {
  if (!Array.isArray(peakHours) || peakHours.length === 0) return [];
  if (typeof peakHours[0] === "number") {
    return peakHours.map((count, hour) => ({
      hour: Number(hour) || 0,
      count: Number(count) || 0,
    }));
  }
  return peakHours
    .map((item) => ({
      hour: Number(item?.hour) || 0,
      count: Number(item?.count) || 0,
    }))
    .sort((a, b) => a.hour - b.hour);
}

function getTopProgram(programDistribution = []) {
  if (!programDistribution.length) return { label: "No dominant program yet", value: 0 };
  const top = programDistribution[0];
  const normalizedProgram = normalizeProgramLabel(top?.program);
  return {
    label: normalizedProgram,
    value: top?.count || 0,
  };
}

function normalizeProgramLabel(value) {
  const normalized = String(value || "").trim();
  if (!normalized) return "Visitor";
  const lowered = normalized.toLowerCase();
  if (lowered === "unknown" || lowered === "n/a" || lowered === "na" || lowered === "-") {
    return "Visitor";
  }
  return normalized;
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
    "unknown:student": "Visitor",
    "unknown student": "Visitor",
    visitor: "Visitor",
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

function buildChartJsConfigForPdf({ options, series }) {
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
          borderWidth: 2.5,
          hoverOffset: 8,
        },
      ]
    : normalizedSeries.map((entry, index) => {
        const color = colors[index % colors.length];
        const rawData = Array.isArray(entry?.data) ? entry.data : [];
        const pointRadius = typeof options?.markers?.size === "number" ? options.markers.size + 1 : 1;
        const isFilled = type === "area" || Boolean(options?.fill) || isRadar;
        const strokeWidth = typeof options?.stroke?.width === "number" ? options.stroke.width + 1 : 4;

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
          pointRadius: isRadar ? Math.max(pointRadius, 7) : pointRadius,
          pointHoverRadius: isRadar
            ? Math.max(pointRadius + 6, 12)
            : pointRadius > 0
              ? pointRadius + 3
              : 6,
          pointHitRadius: isRadar ? 32 : isLineLike ? 20 : pointRadius + 6,
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
            font: { size: 16, weight: "600" },
            padding: 18,
            boxWidth: 16,
            usePointStyle: true,
            pointStyle: "circle",
          },
        },
        tooltip: {
          backgroundColor: "rgba(15, 23, 42, 0.94)",
          titleColor: "#f8fafc",
          bodyColor: "#e2e8f0",
          borderColor: "rgba(148, 163, 184, 0.18)",
          borderWidth: 1,
          titleFont: { size: 14, weight: "600" },
          bodyFont: { size: 13 },
          padding: 12,
          displayColors: true,
          callbacks: isPieLike
            ? {
                label: (ctx) => {
                  const value = ctx?.parsed ?? 0;
                  return ` ${value} visits`;
                },
              }
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
        top: 18,
        right: 22,
        bottom: 18,
        left: 22,
      },
    };
    config.options.scales = {
      r: {
        angleLines: { color: ANALYTICS_COLORS.grid, lineWidth: 1.5 },
        grid: { color: ANALYTICS_COLORS.grid, lineWidth: 1.5 },
        pointLabels: {
          color: ANALYTICS_COLORS.muted,
          font: { size: 14, weight: "600" },
          padding: 8,
        },
        ticks: {
          display: true,
          color: ANALYTICS_COLORS.muted,
          font: { size: 12, weight: "500" },
          backdropColor: "transparent",
          padding: 8,
        },
      },
    };
  } else {
    config.options.scales = {
      x: {
        grid: { display: false },
        ticks: {
          color: ANALYTICS_COLORS.muted,
          font: { size: 14, weight: "500" },
          maxRotation: isHorizontalBar ? 0 : 45,
          autoSkip: true,
          maxTicksLimit: 12,
        },
      },
      y: {
        beginAtZero: true,
        grid: { color: ANALYTICS_COLORS.grid, lineWidth: 1 },
        ticks: {
          color: ANALYTICS_COLORS.muted,
          font: { size: 14, weight: "500" },
          padding: 10,
        },
        title: options?.yaxis?.title?.text
          ? {
              display: true,
              text: options.yaxis.title.text,
              color: ANALYTICS_COLORS.muted,
              font: { size: 14, weight: "600" },
              padding: 12,
            }
          : undefined,
      },
    };
  }

  return config;
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
      const [analyticsResult, realtimeResult] = await Promise.allSettled([
        fetchJson("/api/analytics-reports"),
        fetchJson("/api/dashboard?filter=today"),
      ]);

      let analyticsPayload;
      let analyticsMode = "full";

      if (analyticsResult.status === "fulfilled") {
        analyticsPayload = analyticsResult.value;
      } else {
        analyticsMode = "basic";
        analyticsPayload = await fetchJson("/api/analytics-basic");
      }

      const mergedPayload = {
        ...(analyticsPayload || {}),
        analytics_mode: analyticsMode,
        realtime_status:
          realtimeResult.status === "fulfilled" ? realtimeResult.value : null,
      };

      const refreshedAt = Date.now();
      const serialized = JSON.stringify(mergedPayload);

      if (serialized !== lastPayloadRef.current) {
        lastPayloadRef.current = serialized;
        setBasicData(mergedPayload);
      }

      writeAnalyticsCache(mergedPayload, refreshedAt);
      setLastUpdatedAt(new Date(refreshedAt));
      setLoading(false);
    } catch (err) {
      console.error("Analytics pipeline error:", err);
      setErrorMessage(
        getErrorMessage(
          err,
          "Failed to load analytics data. Check the analytics API or try syncing again.",
        ),
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

    function handleVisibilityChange() {
      if (!document.hidden) {
        runAnalyticsPipeline({ silent: true });
      }
    }

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
      ([entry]) => el.classList.toggle("shadow-sm", !entry.isIntersecting),
      { threshold: 1 },
    );

    const sentinel = document.createElement("div");
    el.parentNode.insertBefore(sentinel, el);
    obs.observe(sentinel);

    return () => {
      obs.disconnect();
    };
  }, []);

  const currentData = basicData;
  const dq = currentData?.data_quality;
  const stats = currentData?.descriptive_stats;
  const dowLabels = currentData?.dow_labels || [];
  const dowAverages = currentData?.dow_averages || [];
  const last30Labels = currentData?.last_30_labels || [];
  const last30Counts = currentData?.last_30_counts || [];

  const totalRemoved =
    typeof dq?.total_removed === "number"
      ? dq.total_removed
      : Number(dq?.removed_low_conf || 0)
          + Number(dq?.removed_outside_hrs || 0)
          + Number(dq?.removed_duplicates || 0);

  const noAnalyticsData = Boolean(currentData?.message && !currentData?.data_quality);
  const initialLoadFailed = !loading && !basicData && error;
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

  const peakHours = normalizePeakHours(currentData?.peak_hours || []);
  const programDistribution = currentData?.program_distribution || [];

  const genderData = (currentData?.gender_data || [])
    .map((item) => ({
      gender: item?.gender || item?.label || "Unknown",
      count: Number(item?.count || 0),
    }))
    .filter((item) => {
      const normalized = String(item.gender || "").trim().toLowerCase();
      return item.count > 0 && !["", "unknown", "n/a", "na", "-"].includes(normalized);
    });

  const yearLevelData = normalizeYearLevelData(currentData?.year_level_data || []);
  const trendDirection = getTrendDirection(last30Counts);
  const peakDow = getPeakDow(dowLabels, dowAverages);
  const peakHour = getPeakHour(peakHours);
  const topProgram = getTopProgram(programDistribution);
  const insightCards = buildInsightCards({
    ...currentData,
    peak_hours: peakHours,
    gender_data: genderData,
    year_level_data: yearLevelData,
  });

  const last30Total = (last30Counts || []).reduce((sum, value) => sum + Number(value || 0), 0);
  const weekdayAverage =
    dowAverages.length > 0
      ? (dowAverages.reduce((sum, value) => sum + Number(value || 0), 0) / dowAverages.length).toFixed(1)
      : "0.0";

  const realtime = currentData?.realtime_status || null;
  const currentOccupancy = Number(realtime?.current_occupancy || 0);
  const maxOccupancy = Number(realtime?.max_occupancy || 0);
  const occupancyRatio = maxOccupancy > 0 ? currentOccupancy / maxOccupancy : 0;
  const occupancyPct = Math.max(0, Math.min(100, Math.round(occupancyRatio * 100)));
  const occupancyStatus =
    realtime?.occupancy_status
      || (occupancyRatio >= 0.9
        ? "Approaching capacity"
        : occupancyRatio >= 0.7
          ? "Moderately busy"
          : "Available");

  const entriesToday = Number(realtime?.total_entries ?? realtime?.today_logs ?? 0);
  const exitsToday = Number(realtime?.total_exits || 0);
  const avgConfidencePct = Number(realtime?.avg_confidence || 0);

  const confidenceEligibleLive = Number(dq?.total_live_confidence_eligible || 0);
  const recognitionAccuracy = confidenceEligibleLive > 0
    ? Math.max(
        0,
        Math.min(100, Number(dq?.avg_live_confidence || 0)),
      )
    : avgConfidencePct;

  const reliabilityScore = Number(dq?.quality_score || 0);

  const forecast = currentData?.forecast || {};
  const allForecasts = currentData?.all_forecasts || [];
  const forecastComparison = currentData?.forecast_comparison || [];
  const bestForecastModel = currentData?.best_forecast_model || "";
  const forecastComparisonInterpretation = currentData?.forecast_comparison_interpretation || "";

  const regression = currentData?.regression || {};
  const regressionInterpretation = currentData?.regression_interpretation || "";
  const clustering = currentData?.clustering || {};
  const clusteringInterpretation = currentData?.clustering_interpretation || "";
  const chiSquare = currentData?.chi_square || {};
  const chiSquareInterpretation = currentData?.chi_square_interpretation || "";
  const correlation = currentData?.correlation || {};
  const correlationInterpretation = currentData?.correlation_interpretation || "";
  const anova = currentData?.anova || {};
  const anovaInterpretation = currentData?.anova_interpretation || "";
  const segmentation = currentData?.segmentation || null;
  const anomalies = currentData?.anomalies || [];

  const forecastValues = forecast?.values || [];
  const forecastLabels = forecast?.labels || [];
  const forecastTotalWeek = forecastValues.reduce((sum, value) => sum + Number(value || 0), 0);
  const forecastPeakIndex = forecastValues.length
    ? forecastValues.indexOf(Math.max(...forecastValues))
    : -1;
  const forecastPeakDay = forecastPeakIndex >= 0
    ? forecastLabels[forecastPeakIndex]
    : "No projection yet";

  const forecastSeries = forecastValues.length
    ? [{ name: "Predicted visits", data: forecastValues }]
    : [];

  const forecastOptions = {
    chart: { type: "bar" },
    colors: [ANALYTICS_COLORS.primary],
    plotOptions: {
      bar: {
        borderRadius: 8,
        columnWidth: "58%",
      },
    },
    xaxis: {
      categories: forecastLabels,
    },
    yaxis: {
      title: { text: "Visits" },
    },
    legend: { show: false },
  };

  const visitsTrendSeries = [{ name: "Daily visits", data: last30Counts || [] }];
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

  const dowSeries = [{ name: "Average visits", data: dowAverages || [] }];
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

  const peakHourSeries = [{
    name: "Visits",
    data: peakHours.map((item) => Number(item?.count || 0)),
  }];
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

  const programSeries = [{
    name: "Visits",
    data: programDistribution.slice(0, 6).map((item) => Number(item?.count || 0)),
  }];
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
      categories: programDistribution.slice(0, 6).map((item) => normalizeProgramLabel(item?.program)),
    },
    legend: { show: false },
  };

  const genderSeries = genderData.map((item) => Number(item?.count || 0));
  const genderOptions = {
    chart: { type: "donut" },
    colors: ["#1d4ed8", "#0f766e", "#f59e0b", "#be123c", "#6d28d9"],
    labels: genderData.map((item) => item?.gender || "Unknown"),
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
              formatter: () => `${genderSeries.reduce((sum, value) => sum + Number(value || 0), 0)}`,
            },
          },
        },
      },
    },
  };

  const yearSeries = [{
    name: "Students",
    data: yearLevelData.slice(0, 6).map((item) => Number(item?.count || 0)),
  }];
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
          const subset = yearLevelData.slice(0, 6);
          const item = subset[dataPointIndex];
          const label = item?.year_level || "Unknown";
          return `${label}: ${value} students`;
        },
      },
    },
  };

  const trendLabel =
    trendDirection === "upward"
      ? "Increasing"
      : trendDirection === "downward"
        ? "Declining"
        : "Stable";
  const trendDeltaPct = getTrendDeltaPercent(last30Counts);

  const heroGradient =
    trendDirection === "upward"
      ? "linear-gradient(135deg, #0f172a 0%, #1e3a8a 46%, #0f766e 100%)"
      : trendDirection === "downward"
        ? "linear-gradient(135deg, #0f172a 0%, #7f1d1d 46%, #9f1239 100%)"
        : "linear-gradient(135deg, #0f172a 0%, #1f2937 46%, #1d4ed8 100%)";

  const keyInsights = [
    `Attendance is currently ${trendLabel.toLowerCase()} over the most recent 30-day window.`,
    `${peakDow.label} is the busiest day, and ${peakHour.label} is the main arrival period.`,
    `${topProgram.label} has the strongest representation in library activity records.`,
  ];

  if (maxOccupancy > 0) {
    keyInsights.push(
      `Real-time occupancy is ${currentOccupancy} of ${maxOccupancy} (${occupancyPct}%), currently marked as ${occupancyStatus.toLowerCase()}.`,
    );
  }

  const recommendations = [
    `Align staffing and circulation support close to ${peakHour.label} on ${peakDow.label}.`,
    `Prepare service capacity for a projected ${fmt(forecastTotalWeek)} visits in the next 7 days.`,
    reliabilityScore < 88
      ? "Prioritize camera positioning and capture-quality checks to raise reporting reliability."
      : "Maintain current data collection and cleaning controls to preserve high reporting reliability.",
  ];

  if (segmentation?.rare_count >= segmentation?.regular_count) {
    recommendations.push(
      "Target low-frequency users with program-level outreach and faculty coordination to improve engagement.",
    );
  }

  const exportCharts = React.useMemo(() => {
    const charts = [];
    if (visitsTrendSeries?.length) {
      charts.push({
        id: "daily-attendance",
        title: "Daily Attendance Trends",
        subtitle: "Last 30 days of validated attendance records",
        options: visitsTrendOptions,
        series: visitsTrendSeries,
        interpretation: `Attendance is ${trendLabel.toLowerCase()} with ${fmt(last30Total)} visits recorded across the 30-day window.`,
      });
    }
    if (dowSeries?.length) {
      charts.push({
        id: "weekday-usage",
        title: "Weekday Usage Patterns",
        subtitle: "Average attendance by day of week",
        options: dowOptions,
        series: dowSeries,
        interpretation: `${peakDow.label} shows the highest average attendance profile in the observed period.`,
      });
    }
    if (peakHourSeries?.length) {
      charts.push({
        id: "peak-hours",
        title: "Peak Hour Distribution",
        subtitle: "Hourly arrivals across the operating window",
        options: peakHourOptions,
        series: peakHourSeries,
        interpretation: `The strongest arrival concentration occurs near ${peakHour.label}.`,
      });
    }
    if (programSeries?.length) {
      charts.push({
        id: "program-distribution",
        title: "Program Distribution",
        subtitle: "Top programs by recorded library visits",
        options: programOptions,
        series: programSeries,
        interpretation: `${topProgram.label} currently leads program-level library activity.`,
      });
    }
    if (yearSeries?.length) {
      charts.push({
        id: "year-level-participation",
        title: "Year-Level Participation",
        subtitle: "Engagement profile by academic year level",
        options: yearOptions,
        series: yearSeries,
        interpretation: `Year-level activity indicates stronger participation among upper-volume cohorts for targeted scheduling.`,
      });
    }
    if (genderSeries?.length) {
      charts.push({
        id: "gender-composition",
        title: "Gender Composition",
        subtitle: "Profiled user composition in attendance records",
        options: genderOptions,
        series: genderSeries,
        interpretation: "Gender composition remains useful for demographic-level service planning and inclusivity monitoring.",
      });
    }
    if (forecastSeries?.length) {
      charts.push({
        id: "forecast-traffic",
        title: "Forecasted Library Traffic",
        subtitle: "Predicted demand for the next 7 days",
        options: forecastOptions,
        series: forecastSeries,
        interpretation: `Projected weekly traffic totals ${fmt(forecastTotalWeek)} visits, with ${forecastPeakDay} as the expected peak day.`,
      });
    }
    return charts;
  }, [
    visitsTrendSeries,
    visitsTrendOptions,
    trendLabel,
    last30Total,
    dowSeries,
    dowOptions,
    peakDow.label,
    peakHourSeries,
    peakHourOptions,
    peakHour.label,
    programSeries,
    programOptions,
    topProgram.label,
    yearSeries,
    yearOptions,
    genderSeries,
    genderOptions,
    forecastSeries,
    forecastOptions,
    forecastTotalWeek,
    forecastPeakDay,
  ]);

  const exportReportContext = React.useMemo(() => ({
    branding: {
      generatedBy: session?.username
        ? `Analytics Console (${session.username})`
        : EXPORT_BRANDING_DEFAULTS.generatedBy,
    },
    trendDirection,
    trendDeltaPct,
    trendLabel,
    totalVisits: Number(currentData?.total_cleaned_logs || 0),
    avgDailyVisitors: Number(stats?.mean_daily_visits || 0),
    currentOccupancy,
    maxOccupancy,
    occupancyPct,
    occupancyStatus,
    entriesToday,
    exitsToday,
    recognitionAccuracy,
    reliabilityScore,
    peakDow,
    peakHour,
    topProgram,
    peakHours,
    programDistribution,
    genderData,
    yearLevelData,
    last30Labels,
    last30Counts,
    forecastValues,
    forecastLabels,
    forecastTotalWeek,
    forecastPeakDay,
    forecastModel: bestForecastModel || forecast?.model || "Not available",
    charts: exportCharts,
    keyInsights,
    recommendations,
  }), [
    session?.username,
    trendDirection,
    trendDeltaPct,
    trendLabel,
    currentData?.total_cleaned_logs,
    stats?.mean_daily_visits,
    currentOccupancy,
    maxOccupancy,
    occupancyPct,
    occupancyStatus,
    entriesToday,
    exitsToday,
    recognitionAccuracy,
    reliabilityScore,
    peakDow,
    peakHour,
    topProgram,
    peakHours,
    programDistribution,
    genderData,
    yearLevelData,
    last30Labels,
    last30Counts,
    forecastValues,
    forecastLabels,
    forecastTotalWeek,
    forecastPeakDay,
    bestForecastModel,
    forecast?.model,
    exportCharts,
    keyInsights,
    recommendations,
  ]);

  const hasAdvancedAnalytics = Boolean(
    forecastValues.length
      || regression?.r2 != null
      || clustering?.cluster_summary?.length
      || segmentation?.segment_counts?.length
      || anomalies.length
      || chiSquare?.chi2 != null
      || anova?.f_stat != null,
  );

  if (hasRenderableData && !noAnalyticsData) {
    return (
      <section className="section">
        <div className="pagetitle">
          <h1>Library Analytics & Reports</h1>
          <nav>
            <ol className="breadcrumb mb-0">
              <li className="breadcrumb-item">
                <a href="/dashboard">Home</a>
              </li>
              <li className="breadcrumb-item active">Analytics Reports</li>
            </ol>
          </nav>
        </div>

        <div
          ref={headerRef}
          style={{
            position: "sticky",
            top: APP_HEADER_HEIGHT,
            zIndex: 100,
            background: "#f3f6fb",
            padding: "12px 0 14px",
            transition: "box-shadow 0.2s",
            marginBottom: 18,
          }}
        >
          <div
            style={{
              background: "rgba(255,255,255,0.92)",
              borderRadius: 20,
              padding: "14px 18px",
              border: `1px solid ${ANALYTICS_COLORS.border}`,
              boxShadow: "0 10px 28px rgba(15, 23, 42, 0.08)",
              backdropFilter: "blur(8px)",
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
                      borderRadius: 11,
                      background: "linear-gradient(135deg, rgba(29,78,216,0.16), rgba(15,118,110,0.16))",
                      color: ANALYTICS_COLORS.primary,
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 15,
                    }}
                  >
                    <i className="bi bi-building"></i>
                  </div>
                  <div>
                    <h5 className="card-title" style={{ padding: 0, margin: 0, color: ANALYTICS_COLORS.ink }}>
                      Executive Library Intelligence Dashboard
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
                  <span>{currentData?.analytics_mode === "full" ? "Advanced analytics enabled" : "Basic analytics mode"}</span>
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
                <ExportModal
                  data={currentData}
                  reportContext={exportReportContext}
                  disabled={loading || refreshing || !hasRenderableData}
                />
              </div>
            </div>
          </div>
        </div>

        {error ? (
          <div className="alert alert-danger">
            <i className="bi bi-exclamation-triangle me-2"></i>
            {errorMessage || "Failed to refresh analytics. Showing the latest available data."}
          </div>
        ) : null}

        <div
          style={{
            background: heroGradient,
            borderRadius: 28,
            padding: "24px 22px",
            boxShadow: "0 24px 56px rgba(15, 23, 42, 0.2)",
            overflow: "hidden",
            position: "relative",
            marginBottom: 18,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              gap: 16,
              flexWrap: "wrap",
              position: "relative",
              zIndex: 2,
            }}
          >
            <div style={{ flex: "1 1 320px", maxWidth: 640 }}>
              <div
                style={{
                  fontSize: 12,
                  letterSpacing: "0.11em",
                  textTransform: "uppercase",
                  color: "rgba(255,255,255,0.72)",
                  fontWeight: 700,
                  marginBottom: 10,
                }}
              >
                1. Executive Summary
              </div>
              <div
                style={{
                  color: "#fff",
                  fontSize: 33,
                  fontWeight: 900,
                  lineHeight: 1.06,
                  maxWidth: 560,
                }}
              >
                Institutional analytics focused on usage, engagement, and operational decisions.
              </div>
              <div
                style={{
                  color: "rgba(255,255,255,0.82)",
                  marginTop: 12,
                  lineHeight: 1.68,
                  fontSize: 13.5,
                  maxWidth: 580,
                }}
              >
                This report combines live recognition events and historical records into a presentation-ready view for library administration, academic leadership, and stakeholder meetings.
              </div>
            </div>

            <div
              style={{
                flex: "1 1 340px",
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
                gap: 12,
                alignSelf: "stretch",
              }}
            >
              <SummaryStat
                label="Total Student Visits"
                value={fmt(currentData?.total_cleaned_logs || 0)}
                tone={ANALYTICS_COLORS.primary}
                icon="bi bi-people"
                helper="Unique cleaned daily visit records"
              />
              <SummaryStat
                label="Peak Usage"
                value={`${peakDow.label}`}
                tone={ANALYTICS_COLORS.secondary}
                icon="bi bi-clock"
                helper={`${peakHour.label} arrival peak`}
              />
              <SummaryStat
                label="Trend Direction"
                value={trendLabel}
                tone={ANALYTICS_COLORS.accent}
                icon="bi bi-graph-up"
                helper={`30-day observed pattern`}
              />
              <SummaryStat
                label="Reliability Score"
                value={`${reliabilityScore}%`}
                tone={ANALYTICS_COLORS.rose}
                icon="bi bi-shield-check"
                helper={`${fmt(totalRemoved)} records filtered`}
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
            background: "#fff",
            borderRadius: 22,
            border: `1px solid ${ANALYTICS_COLORS.border}`,
            padding: 20,
            boxShadow: "0 14px 36px rgba(15, 23, 42, 0.08)",
            marginBottom: 18,
          }}
        >
          <div style={{ fontSize: 17, fontWeight: 800, color: ANALYTICS_COLORS.ink }}>
            Library Performance Overview
          </div>
          <div
            style={{
              marginTop: 6,
              color: ANALYTICS_COLORS.muted,
              fontSize: 12.5,
              lineHeight: 1.6,
            }}
          >
            High-level institutional performance indicators for planning, resource allocation, and service improvements.
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))",
              gap: 14,
              marginTop: 14,
            }}
          >
            <div
              style={{
                background: "#f8fafc",
                borderRadius: 16,
                border: `1px solid ${ANALYTICS_COLORS.border}`,
                padding: 14,
              }}
            >
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: "0.05em", color: ANALYTICS_COLORS.muted, fontWeight: 700 }}>
                Key Insights
              </div>
              <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                {keyInsights.map((line, idx) => (
                  <div
                    key={idx}
                    style={{
                      padding: "9px 10px",
                      background: "#fff",
                      borderRadius: 10,
                      border: `1px solid ${ANALYTICS_COLORS.border}`,
                      color: ANALYTICS_COLORS.ink,
                      fontSize: 12.8,
                      lineHeight: 1.55,
                    }}
                  >
                    <i className="bi bi-lightbulb me-2" style={{ color: ANALYTICS_COLORS.primary }}></i>
                    {line}
                  </div>
                ))}
              </div>
            </div>

            <div
              style={{
                background: "#f8fafc",
                borderRadius: 16,
                border: `1px solid ${ANALYTICS_COLORS.border}`,
                padding: 14,
              }}
            >
              <div style={{ fontSize: 12, textTransform: "uppercase", letterSpacing: "0.05em", color: ANALYTICS_COLORS.muted, fontWeight: 700 }}>
                Recommendations
              </div>
              <div style={{ display: "grid", gap: 8, marginTop: 10 }}>
                {recommendations.map((line, idx) => (
                  <div
                    key={idx}
                    style={{
                      padding: "9px 10px",
                      background: "#fff",
                      borderRadius: 10,
                      border: `1px solid ${ANALYTICS_COLORS.border}`,
                      color: ANALYTICS_COLORS.ink,
                      fontSize: 12.8,
                      lineHeight: 1.55,
                    }}
                  >
                    <i className="bi bi-check2-circle me-2" style={{ color: ANALYTICS_COLORS.secondary }}></i>
                    {line}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div style={{ marginBottom: 10, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 800, color: ANALYTICS_COLORS.muted }}>
          2. Real-Time Library Status
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))",
            gap: 12,
            marginBottom: 18,
          }}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: 18,
              border: `1px solid ${ANALYTICS_COLORS.border}`,
              padding: "16px 16px 14px",
              boxShadow: "0 12px 28px rgba(15, 23, 42, 0.07)",
            }}
          >
            <div style={{ fontSize: 12, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 700 }}>
              Occupancy Monitoring
            </div>
            <div style={{ marginTop: 8, display: "flex", alignItems: "baseline", gap: 8 }}>
              <span style={{ fontSize: 31, fontWeight: 900, color: ANALYTICS_COLORS.ink }}>
                {fmt(currentOccupancy)}
              </span>
              <span style={{ color: ANALYTICS_COLORS.muted, fontSize: 13 }}>
                of {fmt(maxOccupancy || 0)} seats
              </span>
            </div>
            <div
              style={{
                marginTop: 10,
                height: 8,
                background: "#e2e8f0",
                borderRadius: 999,
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${occupancyPct}%`,
                  height: "100%",
                  background:
                    occupancyRatio >= 0.9
                      ? "linear-gradient(90deg, #dc2626, #be123c)"
                      : occupancyRatio >= 0.7
                        ? "linear-gradient(90deg, #d97706, #f59e0b)"
                        : "linear-gradient(90deg, #1d4ed8, #0f766e)",
                }}
              ></div>
            </div>
            <div style={{ marginTop: 8, fontSize: 12.5, color: ANALYTICS_COLORS.muted }}>
              <strong style={{ color: ANALYTICS_COLORS.ink }}>{occupancyPct}% utilized</strong> · {occupancyStatus}
            </div>
          </div>

          {[
            {
              label: "Entries Today",
              value: fmt(entriesToday),
              helper: "Current day recognized entries",
              color: ANALYTICS_COLORS.primary,
              icon: "bi-box-arrow-in-right",
            },
            {
              label: "Exits Today",
              value: fmt(exitsToday),
              helper: "Current day recognized exits",
              color: ANALYTICS_COLORS.secondary,
              icon: "bi-box-arrow-left",
            },
            {
              label: "Recognition Accuracy",
              value: `${recognitionAccuracy.toFixed(1)}%`,
              helper: "Average live confidence",
              color: ANALYTICS_COLORS.gold,
              icon: "bi-bullseye",
            },
          ].map((card) => (
            <div
              key={card.label}
              style={{
                background: "#fff",
                borderRadius: 18,
                border: `1px solid ${ANALYTICS_COLORS.border}`,
                padding: "16px 16px 14px",
                boxShadow: "0 12px 28px rgba(15, 23, 42, 0.07)",
              }}
            >
              <div
                style={{
                  width: 34,
                  height: 34,
                  borderRadius: 11,
                  background: `${card.color}1a`,
                  color: card.color,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontSize: 15,
                  marginBottom: 11,
                }}
              >
                <i className={`bi ${card.icon}`}></i>
              </div>
              <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.05em", fontWeight: 700 }}>
                {card.label}
              </div>
              <div style={{ marginTop: 8, fontSize: 25, fontWeight: 900, color: ANALYTICS_COLORS.ink }}>
                {card.value}
              </div>
              <div style={{ marginTop: 6, fontSize: 12, color: ANALYTICS_COLORS.muted }}>
                {card.helper}
              </div>
            </div>
          ))}
        </div>

        <div style={{ marginBottom: 10, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 800, color: ANALYTICS_COLORS.muted }}>
          3. Attendance & Usage Trends
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(290px, 1fr))",
            gap: 16,
            marginBottom: 16,
          }}
        >
          <ApexChartPanel
            title="Daily Attendance Trend (Last 30 Days)"
            subtitle={`${fmt(last30Total)} visits recorded in this window`}
            height={340}
            options={visitsTrendOptions}
            series={visitsTrendSeries}
          />

          <div
            style={{
              background: "#fff",
              borderRadius: 24,
              border: `1px solid ${ANALYTICS_COLORS.border}`,
              padding: 20,
              boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)",
            }}
          >
            <div style={{ fontSize: 17, fontWeight: 800, color: ANALYTICS_COLORS.ink }}>
              Attendance Interpretation
            </div>
            <div style={{ marginTop: 10, color: ANALYTICS_COLORS.muted, lineHeight: 1.75, fontSize: 13 }}>
              <p style={{ margin: 0 }}>
                The current attendance direction is <strong style={{ color: ANALYTICS_COLORS.ink }}>{trendLabel.toLowerCase()}</strong>, with an average of <strong style={{ color: ANALYTICS_COLORS.ink }}>{stats?.mean_daily_visits || 0}</strong> visits per day.
              </p>
              <p style={{ margin: "10px 0 0" }}>
                The most active day remains <strong style={{ color: ANALYTICS_COLORS.ink }}>{peakDow.label}</strong>, and the main arrival window is <strong style={{ color: ANALYTICS_COLORS.ink }}>{peakHour.label}</strong>.
              </p>
              <p style={{ margin: "10px 0 0" }}>
                Weekday average attendance is <strong style={{ color: ANALYTICS_COLORS.ink }}>{weekdayAverage}</strong> visits, supporting schedule-based planning for staffing and support services.
              </p>
            </div>
            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: 10,
                marginTop: 14,
              }}
            >
              <div style={{ fontSize: 20, fontWeight: 800, color: ANALYTICS_COLORS.primary }}>
                {fmt(stats?.max_daily_visits || 0)}
              </div>
              <div style={{ fontSize: 20, fontWeight: 800, color: ANALYTICS_COLORS.secondary }}>
                {fmt(stats?.std_dev || 0)}
              </div>
              <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, marginTop: -6 }}>
                Highest single-day volume
              </div>
              <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, marginTop: -6 }}>
                Daily variability indicator
              </div>
            </div>
          </div>
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 16,
            marginBottom: 18,
          }}
        >
          <ApexChartPanel
            title="Attendance by Weekday"
            subtitle={`${peakDow.label} leads the weekly usage profile`}
            height={300}
            options={dowOptions}
            series={dowSeries}
          />
          <ApexChartPanel
            title="Usage by Hour"
            subtitle={`${peakHour.label} is the primary arrival period`}
            height={300}
            options={peakHourOptions}
            series={peakHourSeries}
          />
        </div>

        <div style={{ marginBottom: 10, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 800, color: ANALYTICS_COLORS.muted }}>
          4. Student Engagement Analytics
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))",
            gap: 16,
            marginBottom: 16,
          }}
        >
          <ApexChartPanel
            title="Most Active Programs"
            subtitle={`${topProgram.label} currently shows the highest participation`}
            height={320}
            options={programOptions}
            series={programSeries}
          />

          {genderSeries.length > 0 ? (
            <ApexChartPanel
              title="Gender Composition"
              subtitle="Profile composition from available visitor records"
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
              <div style={{ marginTop: 12, color: ANALYTICS_COLORS.muted, lineHeight: 1.7, fontSize: 13 }}>
                Gender fields are still limited in the current dataset. As additional profiles are captured and imported, this view will become more representative.
              </div>
            </div>
          )}
        </div>

        {yearSeries[0]?.data?.length > 0 ? (
          <div style={{ marginBottom: 16 }}>
            <ApexChartPanel
              title="Year Level Participation"
              subtitle="Engagement distribution by academic year"
              height={330}
              options={yearOptions}
              series={yearSeries}
            />
          </div>
        ) : null}

        {segmentation ? (
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: 12,
              marginBottom: 18,
            }}
          >
            {[
              {
                label: "Regular Users",
                value: segmentation.regular_count,
                note: "3+ visits per week",
                color: "#198754",
                icon: "bi-person-check",
              },
              {
                label: "Occasional Users",
                value: segmentation.occasional_count,
                note: "1-2 visits per week",
                color: "#d97706",
                icon: "bi-person",
              },
              {
                label: "Rare Users",
                value: segmentation.rare_count,
                note: "Below 1 visit per week",
                color: "#dc2626",
                icon: "bi-person-dash",
              },
            ].map((card) => (
              <div
                key={card.label}
                style={{
                  background: "#fff",
                  borderRadius: 14,
                  border: `1px solid ${ANALYTICS_COLORS.border}`,
                  padding: "12px 14px",
                  boxShadow: "0 8px 24px rgba(15, 23, 42, 0.05)",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <i className={`bi ${card.icon}`} style={{ color: card.color }}></i>
                  <span style={{ fontSize: 12, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 700 }}>
                    {card.label}
                  </span>
                </div>
                <div style={{ marginTop: 8, fontSize: 24, fontWeight: 900, color: ANALYTICS_COLORS.ink }}>
                  {fmt(card.value)}
                </div>
                <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted }}>
                  {card.note}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        <div style={{ marginBottom: 10, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 800, color: ANALYTICS_COLORS.muted }}>
          5. Forecasting & Predictions
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(290px, 1fr))",
            gap: 16,
            marginBottom: 18,
          }}
        >
          {forecastSeries.length > 0 ? (
            <ApexChartPanel
              title="Predicted Library Traffic (Next 7 Days)"
              subtitle={`${bestForecastModel || forecast?.model || "Forecast"} model projection`}
              height={320}
              options={forecastOptions}
              series={forecastSeries}
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
                Predicted Library Traffic
              </div>
              <div style={{ marginTop: 12, color: ANALYTICS_COLORS.muted, lineHeight: 1.7, fontSize: 13 }}>
                Forecasting requires additional history before producing reliable projections. Continue collecting live data or import historical records to unlock this section.
              </div>
            </div>
          )}

          <div
            style={{
              background: "#fff",
              borderRadius: 24,
              border: `1px solid ${ANALYTICS_COLORS.border}`,
              padding: 20,
              boxShadow: "0 18px 50px rgba(15, 23, 42, 0.08)",
            }}
          >
            <div style={{ fontSize: 17, fontWeight: 800, color: ANALYTICS_COLORS.ink }}>
              Forecast Summary
            </div>
            <div style={{ marginTop: 12, display: "grid", gap: 10 }}>
              <div style={{ padding: "10px 12px", borderRadius: 12, border: `1px solid ${ANALYTICS_COLORS.border}`, background: "#f8fafc" }}>
                <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 700 }}>
                  Forecasted 7-Day Traffic
                </div>
                <div style={{ marginTop: 5, fontSize: 23, fontWeight: 900, color: ANALYTICS_COLORS.ink }}>
                  {fmt(forecastTotalWeek)} visits
                </div>
              </div>
              <div style={{ padding: "10px 12px", borderRadius: 12, border: `1px solid ${ANALYTICS_COLORS.border}`, background: "#f8fafc" }}>
                <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 700 }}>
                  Predicted Peak Day
                </div>
                <div style={{ marginTop: 5, fontSize: 20, fontWeight: 900, color: ANALYTICS_COLORS.ink }}>
                  {forecastPeakDay}
                </div>
              </div>
              <div style={{ padding: "10px 12px", borderRadius: 12, border: `1px solid ${ANALYTICS_COLORS.border}`, background: "#f8fafc" }}>
                <div style={{ fontSize: 11.5, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 700 }}>
                  Forecast Model
                </div>
                <div style={{ marginTop: 5, fontSize: 17, fontWeight: 800, color: ANALYTICS_COLORS.ink }}>
                  {bestForecastModel || forecast?.model || "Not available"}
                </div>
              </div>
            </div>
            {forecast?.interpretation ? (
              <div style={{ marginTop: 12, fontSize: 12.5, color: ANALYTICS_COLORS.muted, lineHeight: 1.7 }}>
                {forecast.interpretation}
              </div>
            ) : null}
          </div>
        </div>

        <div style={{ marginBottom: 10, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 800, color: ANALYTICS_COLORS.muted }}>
          6. Operational Insights
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
            gap: 12,
            marginBottom: 18,
          }}
        >
          <InsightChip
            icon="bi bi-clock-history"
            color={ANALYTICS_COLORS.accent}
            title="Staffing Priority Window"
            body={`${peakHour.label} is the strongest arrival period with around ${peakHour.value} visits.`}
          />
          <InsightChip
            icon="bi bi-calendar-week"
            color={ANALYTICS_COLORS.secondary}
            title="Weekly Utilization Focus"
            body={`${peakDow.label} consistently records the highest average demand.`}
          />
          <InsightChip
            icon="bi bi-cpu"
            color={ANALYTICS_COLORS.primary}
            title="Traffic Planning Baseline"
            body={`Use the projected ${fmt(forecastTotalWeek)} visits next week for service scheduling.`}
          />
          <InsightChip
            icon="bi bi-exclamation-triangle"
            color={anomalies.length > 0 ? "#dc2626" : ANALYTICS_COLORS.muted}
            title="Behavioral Anomalies"
            body={anomalies.length > 0
              ? `${anomalies.length} anomaly flags detected for calendar validation and operational review.`
              : "No major anomaly spikes detected in the current observed period."}
          />
        </div>

        <div style={{ marginBottom: 10, fontSize: 12, textTransform: "uppercase", letterSpacing: "0.07em", fontWeight: 800, color: ANALYTICS_COLORS.muted }}>
          7. Data Quality & System Reliability
        </div>
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
            gap: 12,
            marginBottom: 18,
          }}
        >
          {[
            {
              label: "Data Reliability Score",
              value: `${reliabilityScore}%`,
              helper: `${fmt(dq?.total_cleaned || 0)} of ${fmt(dq?.total_raw || 0)} records retained`,
              color: reliabilityScore >= 90 ? "#198754" : reliabilityScore >= 80 ? "#d97706" : "#dc2626",
              icon: "bi-shield-check",
            },
            {
              label: "Recognition Confidence",
              value: `${recognitionAccuracy.toFixed(1)}%`,
              helper: `Average of ${fmt(confidenceEligibleLive)} positive-confidence live records${Number(dq?.excluded_zero_conf || 0) > 0 ? `; ${fmt(dq.excluded_zero_conf)} zero-confidence unmatched records ignored` : ""}`,
              color: recognitionAccuracy >= 90 ? "#198754" : recognitionAccuracy >= 80 ? "#d97706" : "#dc2626",
              icon: "bi-camera-video",
            },
            {
              label: "Processing Yield",
              value: `${fmt(dq?.total_cleaned || 0)}`,
              helper: `${fmt(totalRemoved)} excluded records (duplicates, outside hours, low confidence)`,
              color: ANALYTICS_COLORS.primary,
              icon: "bi-funnel",
            },
            {
              label: "Analytics Service Health",
              value: socketConnected ? "Live" : "Polling",
              helper: socketConnected
                ? "WebSocket updates are active"
                : "Running on interval-based sync",
              color: socketConnected ? "#198754" : "#d97706",
              icon: "bi-hdd-network",
            },
          ].map((card) => (
            <div
              key={card.label}
              style={{
                background: "#fff",
                borderRadius: 14,
                border: `1px solid ${ANALYTICS_COLORS.border}`,
                padding: "13px 14px",
                boxShadow: "0 8px 22px rgba(15, 23, 42, 0.05)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <i className={`bi ${card.icon}`} style={{ color: card.color }}></i>
                <span style={{ fontSize: 12, color: ANALYTICS_COLORS.muted, textTransform: "uppercase", letterSpacing: "0.04em", fontWeight: 700 }}>
                  {card.label}
                </span>
              </div>
              <div style={{ marginTop: 8, fontSize: 24, fontWeight: 900, color: card.color }}>
                {card.value}
              </div>
              <div style={{ marginTop: 6, fontSize: 12, color: ANALYTICS_COLORS.muted, lineHeight: 1.55 }}>
                {card.helper}
              </div>
            </div>
          ))}
        </div>

        <details
          style={{
            background: "#fff",
            borderRadius: 18,
            border: `1px solid ${ANALYTICS_COLORS.border}`,
            boxShadow: "0 14px 36px rgba(15, 23, 42, 0.07)",
            padding: "14px 16px",
          }}
        >
          <summary
            style={{
              cursor: "pointer",
              listStyle: "none",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: 10,
            }}
          >
            <span style={{ fontSize: 13, textTransform: "uppercase", letterSpacing: "0.06em", fontWeight: 800, color: ANALYTICS_COLORS.muted }}>
              8. Detailed Technical Analytics
            </span>
            <span style={{ fontSize: 12, color: ANALYTICS_COLORS.muted }}>
              Expand for statistical and model-level diagnostics
            </span>
          </summary>

          <div style={{ marginTop: 14 }}>
            {!hasAdvancedAnalytics ? (
              <div className="alert alert-info" style={{ marginBottom: 14 }}>
                <i className="bi bi-info-circle me-2"></i>
                Detailed model outputs are unavailable in the current payload. Sync again or switch to full analytics mode.
              </div>
            ) : null}

            <Section
              stepNum="T1"
              title="Data Quality Breakdown"
              color="#0d6efd"
              subtitle={`${fmt(dq?.total_raw || 0)} raw -> ${fmt(dq?.total_cleaned || 0)} clean`}
              defaultOpen={false}
            >
              <DataQualitySection dq={dq} />
            </Section>

            <Section
              stepNum="T2"
              title="Exploratory Statistics"
              color="#0ea5e9"
              subtitle={`Mean: ${stats?.mean_daily_visits || 0} visits/day · Std Dev: ${stats?.std_dev || 0}`}
              defaultOpen={false}
            >
              <EDASection
                stats={stats}
                dowLabels={dowLabels}
                dowAverages={dowAverages}
              />
            </Section>

            <Section
              stepNum="T3"
              title="30-Day Technical Trend"
              color="#1d4ed8"
              subtitle="Daily counts with recent mean reference"
              defaultOpen={false}
            >
              <TrendChart labels={last30Labels} counts={last30Counts} />
            </Section>

            <Section
              stepNum="T4"
              title="Forecasting Models"
              color="#1d4ed8"
              subtitle={`${bestForecastModel || "Model comparison"} · 7-day projection`}
              defaultOpen={false}
            >
              <ForecastSection
                forecast={forecast}
                allForecasts={allForecasts}
                comparison={forecastComparison}
                bestModel={bestForecastModel}
                comparisonInterp={forecastComparisonInterpretation}
              />
            </Section>

            <Section
              stepNum="T5"
              title="Linear Regression"
              color="#dc2626"
              subtitle="Trend fit and R² diagnostics"
              defaultOpen={false}
            >
              <LinearRegressionSection
                regression={regression}
                interpretation={regressionInterpretation}
                counts={last30Counts}
                labels={last30Labels}
              />
            </Section>

            <Section
              stepNum="T6"
              title="K-Means Clustering"
              color="#7c3aed"
              subtitle="Behavioral grouping and elbow method"
              defaultOpen={false}
            >
              <ClusteringSection
                clustering={clustering}
                interpretation={clusteringInterpretation}
              />
            </Section>

            <Section
              stepNum="T7"
              title="Statistical Testing"
              color="#0f766e"
              subtitle="Chi-square, Pearson correlation, and ANOVA"
              defaultOpen={false}
            >
              <StatisticalTestsSection
                chiSquare={chiSquare}
                chiInterp={chiSquareInterpretation}
                correlation={correlation}
                corrInterp={correlationInterpretation}
                anova={anova}
                anovaInterp={anovaInterpretation}
              />
            </Section>

            <Section
              stepNum="T8"
              title="Student Segmentation"
              color="#198754"
              subtitle="Regular vs occasional vs rare usage"
              defaultOpen={false}
            >
              <SegmentationSection seg={segmentation} />
            </Section>

            <Section
              stepNum="T9"
              title="Anomaly Detection"
              color="#dc2626"
              subtitle="Z-score based anomaly screening"
              defaultOpen={false}
            >
              <AnomalySection
                anomalies={anomalies}
                mean={stats?.mean_daily_visits || 0}
                stdDev={stats?.std_dev || 0}
              />
            </Section>
          </div>
        </details>
      </section>
    );
  }

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>Library Analytics & Reports</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item active">Analytics Reports</li>
          </ol>
        </nav>
      </div>

      <div className="d-flex align-items-center gap-2 flex-wrap mb-3">
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
        <ExportModal
          data={currentData}
          reportContext={exportReportContext}
          disabled={loading || refreshing || !hasRenderableData}
        />
      </div>

      {loading && !basicData && (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            minHeight: "420px",
            gap: 14,
          }}
        >
          <div
            style={{
              width: 46,
              height: 46,
              borderRadius: "50%",
              border: "4px solid #e9ecef",
              borderTop: "4px solid #0d6efd",
              animation: "spin 1s linear infinite",
            }}
          ></div>
          <div style={{ fontSize: 14, color: "#6c757d", fontWeight: 500 }}>
            Loading analytics dashboard...
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

      {noAnalyticsData && (
        <div className="alert alert-info">
          <i className="bi bi-info-circle me-2"></i>
          {currentData?.message || currentData?.error || "No analytics data available."}
        </div>
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





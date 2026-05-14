import React from "react";
import { Link } from "react-router-dom";
import { getErrorMessage, showAlert, showError, showSuccess } from "../alerts.js";
import { fetchJson } from "../api.js";
import { socket } from "../socket.js";
import "./Dashboard.css";

const PEAK_HOUR_START = 7;
const PEAK_HOUR_END = 19;
const PEAK_HOUR_COUNT = PEAK_HOUR_END - PEAK_HOUR_START + 1;
const FULL_DAY_START = 0;
const FULL_DAY_END = 23;
const FULL_DAY_COUNT = FULL_DAY_END - FULL_DAY_START + 1;
const DAYS_OF_WEEK = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CAPACITY_ALERT_POPUP_COOLDOWN_MS = 45000;
const CAPACITY_ALERT_TIMER_MS = 5000;

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

function normalizeHourWindow(rawData, startHour, endHour) {
  const count = endHour - startHour + 1;
  if (!Array.isArray(rawData)) {
    return normalizeCountList([], count);
  }
  if (rawData.length >= FULL_DAY_COUNT) {
    return normalizeCountList(rawData.slice(startHour, endHour + 1), count);
  }
  return normalizeCountList(rawData, count);
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

function buildHourLabels(startHour, endHour) {
  return Array.from(
    { length: endHour - startHour + 1 },
    (_, idx) => formatHourLabel(startHour + idx),
  );
}

// Stat Card
const DASHBOARD_FILTER_OPTIONS = [
  { value: "today", label: "Today" },
  { value: "last_7_days", label: "Last 7 Days" },
  { value: "last_14_days", label: "Last 14 Days" },
  { value: "last_30_days", label: "Last 30 Days" },
  { value: "last_90_days", label: "Last 90 Days" },
];

const WEEKDAY_SHORT_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function getDashboardViewMode(filterKey, filterDays = null) {
  if (filterKey === "today") return "today";
  if (filterKey === "last_7_days" || filterKey === "last_14_days") return "short";
  return "long";
}

function getDashboardViewLabel(filterKey, filterDays = null) {
  const mode = getDashboardViewMode(filterKey, filterDays);
  if (mode === "today") return "Today Snapshot";
  if (mode === "short") return "Short-Range Traffic";
  return "Long-Range Traffic";
}

function getLocalDateInputValue(value = new Date()) {
  const source = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(source.getTime())) {
    return "";
  }
  const local = new Date(source.getTime() - source.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 10);
}

function getLocalDateFromInput(value) {
  if (value instanceof Date) {
    return new Date(value.getFullYear(), value.getMonth(), value.getDate());
  }
  const parts = String(value || "").split("-").map((part) => Number(part));
  if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) {
    return new Date();
  }
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function shiftDateInput(value, dayOffset) {
  const source = getLocalDateFromInput(value);
  source.setDate(source.getDate() + dayOffset);
  return getLocalDateInputValue(source);
}

function getWeekStartInputValue(value = new Date()) {
  const source = getLocalDateFromInput(value);
  const mondayOffset = (source.getDay() + 6) % 7;
  source.setDate(source.getDate() - mondayOffset);
  return getLocalDateInputValue(source);
}

function formatCompactHeatmapHourLabel(label) {
  return String(label || "").replace(/\s+/g, "").toLowerCase();
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

function parseApiTimestamp(value) {
  if (!value) return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;

  const raw = String(value).trim();
  if (!raw) return null;

  // Normalize backend timestamps to an ISO-like form for consistent parsing.
  let normalized = raw.replace(" ", "T");
  if (!/[zZ]|[+-]\d{2}:\d{2}$/.test(normalized)) {
    normalized = `${normalized}Z`;
  }

  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function formatSnapshotTime(timestamp) {
  if (!timestamp) return "-";
  const parsed = parseApiTimestamp(timestamp);
  if (!parsed) return String(timestamp);
  return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function formatSnapshotTimeWithDate(timestamp) {
  if (!timestamp) return "-";
  const parsed = parseApiTimestamp(timestamp);
  if (!parsed) return String(timestamp);
  const time = parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const date = parsed.toLocaleDateString([], { month: "short", day: "numeric" });
  return `${time} ${date}`;
}

function formatRelativeTime(timestamp) {
  const parsed = parseApiTimestamp(timestamp);
  if (!parsed) return "just now";

  const diffMs = Date.now() - parsed.getTime();
  if (diffMs <= 0) return "just now";

  const diffMinutes = Math.round(diffMs / 60000);
  if (diffMinutes < 1) return "just now";
  if (diffMinutes < 60) return `${diffMinutes}m ago`;

  const diffHours = Math.round(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.round(diffHours / 24);
  return `${diffDays}d ago`;
}

function getPersonInitials(name) {
  const cleaned = String(name || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2);

  if (!cleaned.length) {
    return "?";
  }

  return cleaned.map((part) => part[0]?.toUpperCase() || "").join("");
}

function getConfidenceTone(confidencePercent) {
  const value = toNonNegativeNumber(confidencePercent);
  if (value >= 90) return "success";
  if (value >= 75) return "info";
  if (value >= 60) return "warning";
  return "danger";
}

function normalizeProgramLabel(value) {
  const normalized = String(value || "").trim();
  return normalized || "Unknown";
}

function normalizeGenderDistribution(items = []) {
  const merged = new Map();
  items.forEach((item) => {
    const label = String(item?.gender || item?.label || "Unknown").trim() || "Unknown";
    const count = toNonNegativeNumber(item?.count);
    if (count > 0) {
      merged.set(label, (merged.get(label) || 0) + count);
    }
  });
  return [...merged.entries()].map(([label, count]) => ({ label, count }));
}

function normalizeProgramDistribution(items = []) {
  return items
    .map((item) => ({
      label: normalizeProgramLabel(item?.program || item?.label),
      count: toNonNegativeNumber(item?.count),
    }))
    .filter((item) => item.count > 0);
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
    Visitor: 97,
    Unknown: 99,
  };
  return lookup[normalized] ?? 98;
}

function normalizeYearLevelDistribution(items = []) {
  const merged = new Map();

  items.forEach((item) => {
    const label = normalizeYearLevelLabel(getYearLevelLabel(item));
    const count = toNonNegativeNumber(item?.count);
    if (count > 0) {
      merged.set(label, (merged.get(label) || 0) + count);
    }
  });

  return [...merged.entries()]
    .map(([label, count]) => ({ label, count }))
    .sort((a, b) => {
      const orderDelta = getYearLevelOrder(a.label) - getYearLevelOrder(b.label);
      if (orderDelta !== 0) return orderDelta;
      return a.label.localeCompare(b.label);
    });
}

// Generate plain-language status message for staff
function getOccupancyStatusMessage(occupancyCount, capacityLimit, isFull, capacityWarning) {
  if (isFull) {
    return "At capacity. No new entries permitted.";
  }

  const ratio = capacityLimit > 0 ? occupancyCount / capacityLimit : 0;
  const remainingSlots = Math.max(capacityLimit - occupancyCount, 0);

  if (capacityWarning) {
    return `Near capacity: ${remainingSlots} slot${remainingSlots === 1 ? "" : "s"} available.`;
  }

  if (ratio >= 0.7) {
    return `${remainingSlots} slot${remainingSlots === 1 ? "" : "s"} available. Monitor entry flow.`;
  }

  return `${remainingSlots} slot${remainingSlots === 1 ? "" : "s"} available.`;
}

function normalizeUserTypeDistribution(items = []) {
  const totals = {
    Students: 0,
    Visitors: 0,
    Unrecognized: 0,
  };
  const accents = {
    Students: "green",
    Visitors: "blue",
    Unrecognized: "amber",
  };
  const aliases = {
    enrolled: "Students",
    student: "Students",
    students: "Students",
    visitor: "Visitors",
    visitors: "Visitors",
    unrecognized: "Unrecognized",
  };

  items.forEach((item) => {
    const rawLabel = String(item?.label || item?.user_type || "").trim();
    const normalizedLabel = rawLabel.toLowerCase();
    const canonical = aliases[normalizedLabel];
    if (!canonical) {
      return;
    }
    totals[canonical] += toNonNegativeNumber(item?.count);
  });

  return ["Students", "Visitors", "Unrecognized"].map((label) => ({
    label,
    count: totals[label],
    accent: accents[label],
  }));
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

function formatDirectionLabel(eventType) {
  return String(eventType || "").trim().toLowerCase() === "exit" ? "Exit" : "Entry";
}

function getDirectionTone(eventType) {
  return formatDirectionLabel(eventType) === "Exit" ? "amber" : "green";
}

function formatUserTypeTagLabel(userType) {
  const normalized = String(userType || "").trim().toLowerCase();
  if (normalized === "enrolled" || normalized === "student" || normalized === "students") {
    return "Students";
  }
  if (normalized === "visitor" || normalized === "visitors") {
    return "Visitors";
  }
  if (normalized === "unrecognized") {
    return "Unrecognized";
  }
  if (normalized === "staff") {
    return "Staff";
  }
  return "Unknown";
}

function getUserTypeTone(userType) {
  const normalized = String(userType || "").trim().toLowerCase();
  if (normalized === "enrolled" || normalized === "student" || normalized === "students") {
    return "blue";
  }
  if (normalized === "visitor" || normalized === "visitors") {
    return "green";
  }
  if (normalized === "unrecognized") {
    return "amber";
  }
  return "rose";
}

function buildUserTypeSheetRows(userTypeDistribution = []) {
  const normalizedDistribution = normalizeUserTypeDistribution(userTypeDistribution);
  return [
    ["User Type", "Count"],
    ...normalizedDistribution.map((item) => [item.label, item.count]),
  ];
}

function buildRecentEntriesSheetRows(recentEntries = []) {
  return [
    ["Name", "SR Code", "User Type", "Confidence", "Timestamp", "Status"],
    ...(recentEntries ?? []).map((item) => [
      item.name || "Unknown",
      item.sr_code || "Visitor",
      item.user_type || "unknown",
      `${toNonNegativeNumber(item.conf_pct)}%`,
      item.timestamp || "",
      item.status || "",
    ]),
  ];
}

function buildActiveAlertsSheetRows(activeAlerts = []) {
  return [
    ["Message", "Occupancy", "Capacity", "Percent", "Created At"],
    ...(activeAlerts ?? []).map((alert) => [
      alert.message || "Capacity alert",
      alert.occupancy_count ?? 0,
      alert.capacity_limit ?? 0,
      `${Math.round((Number(alert.occupancy_ratio || 0) || 0) * 100)}%`,
      alert.created_at || "",
    ]),
  ];
}

function buildOccupancyHistorySheetRows(occupancyHistory = []) {
  return [
    ["Snapshot Time", "Occupancy", "Capacity", "Entries", "Exits", "Status"],
    ...(occupancyHistory ?? []).map((snapshot) => [
      snapshot.snapshot_timestamp || "",
      snapshot.occupancy_count ?? 0,
      snapshot.capacity_limit ?? 0,
      snapshot.daily_entries ?? 0,
      snapshot.daily_exits ?? 0,
      snapshot.capacity_warning ? "Warning" : "Normal",
    ]),
  ];
}

function buildDashboardWorkbook(XLSX, data, filterKey = data?.filter_key) {
  const viewMode = getDashboardViewMode(filterKey, data?.filter_days);
  const workbook = XLSX.utils.book_new();
  const filterLabel =
    data?.filter_label ??
    DASHBOARD_FILTER_OPTIONS.find((item) => item.value === filterKey)?.label ??
    "";
  const dateRangeLabel = formatRangeLabel(
    data?.filter_start_date,
    data?.filter_end_date
  );
  const liveOccupancy = data?.live_occupancy ?? null;
  const activeAlerts = data?.active_alerts ?? [];
  const occupancyHistory = data?.occupancy_history ?? [];
  const peakPatternSummary = data?.peak_pattern_summary ?? {};
  const busiestDay = peakPatternSummary?.busiest_day ?? {};
  const busiestHour = peakPatternSummary?.busiest_hour ?? {};

  const summaryRows = [
    ["Dashboard Export"],
    [],
    ["Filter", filterLabel],
    ["Dashboard View", getDashboardViewLabel(filterKey, data?.filter_days)],
    ["Date Range", dateRangeLabel],
    [],
    ["Summary"],
    ["Metric", "Value"],
    ["Registered Students", data?.total_students ?? 0],
    ["Entry Recognition Logs", data?.total_logs ?? 0],
    ["Unique Visitors", data?.unique_visitors ?? 0],
    ["Entries", data?.total_entries ?? 0],
    ["Exits", data?.total_exits ?? 0],
    ["Unrecognized Faces", data?.unrecognized_count ?? 0],
    ["Low-Confidence Events", data?.low_confidence_count ?? 0],
    ["Average Confidence", `${data?.avg_confidence ?? 0}%`],
    ["Active Alerts", activeAlerts.length],
    ["Busiest Day", busiestDay?.label ? `${busiestDay.label} (${busiestDay.count ?? 0} entries)` : "No entries"],
    ["Busiest Hour", busiestHour?.label ? `${busiestHour.label} (${busiestHour.count ?? 0} entries)` : "No entries"],
    ["Average Entries Per Day", peakPatternSummary?.average_daily_visits ?? 0],
    ["Active Days", peakPatternSummary?.active_day_count ?? 0],
  ];

  if (liveOccupancy) {
    summaryRows.push(
      ["Current Occupancy", liveOccupancy.occupancy_count ?? 0],
      ["Capacity Limit", liveOccupancy.capacity_limit ?? 0],
      [
        "Occupancy Status",
        liveOccupancy.is_full
          ? "Full Capacity"
          : liveOccupancy.capacity_warning
            ? "Warning Threshold"
            : "Normal Capacity",
      ]
    );
  }

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

  const genderRows = [
    ["Gender", "Unique Visitors"],
    ...(data?.gender_distribution ?? []).map((item) => [item.gender || "Unknown", item.count]),
  ];
  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, genderRows, { minWidth: 16, freezeTopRow: true }),
    sanitizeWorksheetName("Gender Distribution")
  );

  const yearLevelRows = [
    ["Year Level", "Unique Visitors"],
    ...(data?.year_level_distribution ?? []).map((item) => [item.year_level || "Unknown", item.count]),
  ];
  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, yearLevelRows, { minWidth: 16, freezeTopRow: true }),
    sanitizeWorksheetName("Year Level Distribution")
  );

  const hourRows = [
    ["Hour", "Visits"],
    ...(
      viewMode === "today"
        ? normalizeHourWindow(data?.peak_hours ?? [], FULL_DAY_START, FULL_DAY_END)
        : normalizeHourWindow(data?.peak_hours ?? [], PEAK_HOUR_START, PEAK_HOUR_END)
    ).map((count, hourIndex) => [
      formatHourLabel(
        (viewMode === "today" ? FULL_DAY_START : PEAK_HOUR_START) + hourIndex
      ),
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

  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, buildUserTypeSheetRows(data?.user_type_distribution ?? []), {
      minWidth: 16,
      freezeTopRow: true,
    }),
    sanitizeWorksheetName("User Types")
  );

  XLSX.utils.book_append_sheet(
    workbook,
    buildWorksheet(XLSX, buildRecentEntriesSheetRows(data?.recent_entries ?? []), {
      minWidth: 16,
      freezeTopRow: true,
    }),
    sanitizeWorksheetName("Recent Entries")
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

  if (activeAlerts.length) {
    XLSX.utils.book_append_sheet(
      workbook,
      buildWorksheet(XLSX, buildActiveAlertsSheetRows(activeAlerts), {
        minWidth: 16,
        freezeTopRow: true,
      }),
      sanitizeWorksheetName("Active Alerts")
    );
  }

  if (occupancyHistory.length) {
    XLSX.utils.book_append_sheet(
      workbook,
      buildWorksheet(XLSX, buildOccupancyHistorySheetRows(occupancyHistory), {
        minWidth: 16,
        freezeTopRow: true,
      }),
      sanitizeWorksheetName("Occupancy Timeline")
    );
  }

  return workbook;
}

async function downloadDashboardExport(data, filterKey) {
  const XLSX = await import("xlsx");
  const workbook = buildDashboardWorkbook(XLSX, data, filterKey);
  const normalizedKey = filterKey ?? data?.filter_key ?? "export";
  const dateSuffix = new Date().toISOString().slice(0, 10);
  XLSX.writeFile(workbook, `dashboard-${normalizedKey}-${dateSuffix}.xlsx`);
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

function DashboardMetricCard({ title, value, meta, accent = "blue", badge }) {
  return (
    <article className={`dashboard-metric-card dashboard-accent-${accent}`}>
      <p className="dashboard-metric-title">{title}</p>
      <div className="dashboard-metric-value">{value}</div>
      {badge ? <span className="dashboard-inline-badge">{badge}</span> : null}
      <p className="dashboard-metric-meta">{meta}</p>
    </article>
  );
}

function DashboardLineChart({
  labels,
  values,
  height = 240,
  valueLabel = "items",
  lineColor = "#0072BB",
  fillColor = "rgba(0, 114, 187, 0.24)",
  threshold = null,
  thresholdLabel = "",
}) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !Array.isArray(labels) || !labels.length) {
      return undefined;
    }

    if (chartRef.current) {
      chartRef.current.destroy();
    }

    const safeValues = Array.isArray(values) ? values.map(toNonNegativeNumber) : [];
    const datasets = [
      {
        label: valueLabel,
        data: safeValues,
        borderColor: lineColor,
        backgroundColor: fillColor,
        borderWidth: 3,
        fill: true,
        pointBackgroundColor: lineColor,
        pointBorderColor: "#ffffff",
        pointBorderWidth: 2,
        pointRadius: safeValues.map((_, index) => (index === safeValues.length - 1 ? 5 : 0)),
        pointHoverRadius: safeValues.map((_, index) => (index === safeValues.length - 1 ? 6 : 3)),
        tension: 0.35,
      },
    ];

    if (Number.isFinite(threshold)) {
      datasets.push({
        label: thresholdLabel || "Threshold",
        data: safeValues.map(() => threshold),
        borderColor: "rgba(247, 160, 26, 0.9)",
        borderDash: [6, 6],
        borderWidth: 2,
        fill: false,
        pointRadius: 0,
        pointHoverRadius: 0,
      });
    }

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "line",
      data: {
        labels,
        datasets,
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          intersect: false,
          mode: "index",
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                if (ctx.datasetIndex === 1 && Number.isFinite(threshold)) {
                  return ` ${thresholdLabel || "Threshold"}: ${ctx.parsed.y}`;
                }
                return ` ${ctx.parsed.y} ${valueLabel}`;
              },
            },
          },
        },
        scales: {
          x: {
            grid: { display: false },
            border: { display: false },
            ticks: {
              color: "#4f5d73",
              font: { size: 11, weight: "600" },
              maxTicksLimit: 7,
            },
          },
          y: {
            beginAtZero: true,
            grid: { color: "rgba(79, 93, 115, 0.18)" },
            border: { display: false },
            ticks: {
              color: "#4f5d73",
              font: { size: 11, weight: "600" },
              precision: 0,
            },
          },
        },
      },
    });

    return () => chartRef.current?.destroy();
  }, [fillColor, labels, lineColor, threshold, thresholdLabel, valueLabel, values]);

  if (!Array.isArray(labels) || !labels.length) {
    return <div className="dashboard-empty-state">No data available yet.</div>;
  }

  return (
    <div className="dashboard-line-chart" style={{ height }}>
      <canvas ref={canvasRef}></canvas>
    </div>
  );
}

function DashboardProgressList({
  items,
  labelKey = "label",
  valueKey = "count",
  accent = "blue",
  emptyText = "No data available yet.",
}) {
  if (!Array.isArray(items) || !items.length) {
    return <div className="dashboard-empty-state">{emptyText}</div>;
  }

  const maxValue = Math.max(...items.map((item) => toNonNegativeNumber(item?.[valueKey])), 1);

  return (
    <div className="dashboard-progress-list">
      {items.map((item, index) => {
        const value = toNonNegativeNumber(item?.[valueKey]);
        const width = Math.max(6, Math.round((value / maxValue) * 100));
        const label = item?.[labelKey] ?? `Item ${index + 1}`;
        const itemAccent = item?.accent || accent;

        return (
          <div key={`${label}-${index}`} className="dashboard-progress-row">
            <div className="dashboard-progress-copy">
              <span className="dashboard-progress-label">{label}</span>
              <span className="dashboard-progress-value">{value}</span>
            </div>
            <div className="dashboard-progress-track">
              <div
                className={`dashboard-progress-fill dashboard-accent-${itemAccent}`}
                style={{ width: `${width}%` }}
              ></div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function RecentEntriesList({ entries }) {
  if (!Array.isArray(entries) || !entries.length) {
    return <div className="dashboard-empty-state">No recent entry events yet.</div>;
  }

  return (
    <div className="dashboard-recent-list">
      {entries.map((entry, index) => {
        const name =
          entry?.user_type === "unrecognized" || !String(entry?.name || "").trim()
            ? "Unrecognized User"
            : entry.name;
        const confidenceTone = getConfidenceTone(entry?.conf_pct);
        const directionLabel = formatDirectionLabel(entry?.event_type);
        const directionTone = getDirectionTone(entry?.event_type);
        const userTypeLabel = formatUserTypeTagLabel(entry?.user_type);
        const userTypeTone = getUserTypeTone(entry?.user_type);

        return (
          <div
            key={`${entry.id ?? entry.event_id ?? entry.timestamp ?? index}-${name}`}
            className={`dashboard-recent-item dashboard-confidence-band-${confidenceTone}`}
          >
            <div className={`dashboard-avatar dashboard-accent-${confidenceTone}`}>
              {getPersonInitials(name)}
            </div>
            <div className="dashboard-recent-copy">
              <div className="dashboard-recent-headline">
                <span className="dashboard-recent-name">{name}</span>
              </div>
              <div className="dashboard-recent-meta">
                <span>{entry?.user_type === "unrecognized" ? "N/A" : entry?.sr_code || "Visitor"}</span>
                <span>{formatRelativeTime(entry?.timestamp)}</span>
              </div>
              <div className="dashboard-recent-tags">
                <span className={`dashboard-recent-tag dashboard-accent-${directionTone}`}>
                  {directionLabel}
                </span>
                <span className={`dashboard-recent-tag dashboard-accent-${userTypeTone}`}>
                  {userTypeLabel}
                </span>
                <span className={`dashboard-recent-tag dashboard-tone-${confidenceTone}`}>
                  {toNonNegativeNumber(entry?.conf_pct)}% confidence
                </span>
              </div>
            </div>
          </div>
        );
      })}
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
function PeakHoursChart({ data, startHour = PEAK_HOUR_START, endHour = PEAK_HOUR_END, height = 470 }) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);
  const labels = React.useMemo(
    () => buildHourLabels(startHour, endHour),
    [startHour, endHour]
  );
  const normalizedData = React.useMemo(
    () => normalizeHourWindow(data, startHour, endHour),
    [data, startHour, endHour]
  );

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !normalizedData.length) return;
    if (chartRef.current) chartRef.current.destroy();

    const maxVisits = Math.max(...normalizedData, 0);

    chartRef.current = new window.Chart(canvasRef.current, {
      type: "bar",
      data: {
        labels,
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
  }, [normalizedData, labels]);

  if (!Array.isArray(data) || !data.length)
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  return (
    <div
      className="chart-container"
      style={{ height, position: "relative" }}
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

  const normalizeTopVisitorSrCode = (value) => {
    const normalized = String(value || "").trim();
    if (!normalized) return "Visitor";
    const lowered = normalized.toLowerCase();
    if (lowered === "n/a" || lowered === "na" || lowered === "unknown" || lowered === "-") {
      return "Visitor";
    }
    return normalized;
  };

  const normalizedData = data.map((visitor) => ({
    ...visitor,
    name: visitor?.name || "Unknown",
    sr_code: normalizeTopVisitorSrCode(visitor?.sr_code),
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
function WeeklyHeatmap({
  data,
  weekLabel,
  canGoNext = false,
  onPreviousWeek,
  onNextWeek,
}) {
  if (!data?.length) {
    return (
      <div className="text-muted small text-center py-4">No data available</div>
    );
  }

  const hours = PEAK_HOUR_LABELS.map(formatCompactHeatmapHourLabel);
  const normalizedRows = data.map((row, index) => {
    const day =
      typeof row?.day === "string" && row.day
        ? row.day
        : DAYS_OF_WEEK[index] || `Day ${index + 1}`;
    const values = normalizeCountList(row?.values, PEAK_HOUR_COUNT);
    return {
      day,
      values,
      total: values.reduce((sum, value) => sum + toNonNegativeNumber(value), 0),
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
    <div className="dashboard-heatmap">
      <div className="dashboard-heatmap-toolbar">
        <span className="dashboard-heatmap-week">{weekLabel || "Current week"}</span>
        <div className="dashboard-heatmap-actions">
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={onPreviousWeek}
          >
            Previous
          </button>
          <button
            type="button"
            className="btn btn-outline-secondary btn-sm"
            onClick={onNextWeek}
            disabled={!canGoNext}
          >
            Next
          </button>
        </div>
      </div>
      <div className="dashboard-heatmap-scroll">
        <table className="dashboard-heatmap-table">
          <thead>
            <tr>
              <th
                className="dashboard-heatmap-day-heading"
              >
                Day
              </th>
              {hours.map((h) => (
                <th key={h}>{h}</th>
              ))}
              <th className="dashboard-heatmap-total-heading">Total</th>
            </tr>
          </thead>
          <tbody>
            {normalizedRows.map((row, i) => (
              <tr key={`${row.day}-${i}`}>
                <td className="dashboard-heatmap-day">{row.day}</td>
                {row.values.map((val, j) => (
                  <td key={j}>
                    <div
                      className="dashboard-heatmap-cell"
                      title={`${row.day} ${hours[j]}: ${val} visits`}
                      style={{
                        background: getColor(val),
                        color: getTextColor(val),
                      }}
                      onMouseEnter={(e) =>
                        (e.currentTarget.style.transform = "scale(1.12)")
                      }
                      onMouseLeave={(e) =>
                        (e.currentTarget.style.transform = "scale(1)")
                      }
                    >
                      {val > 0 ? val : ""}
                    </div>
                  </td>
                ))}
                <td className="dashboard-heatmap-total">
                  <span title={`${row.day}: ${row.total} visits`}>
                    {row.total} visit{row.total === 1 ? "" : "s"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="dashboard-heatmap-legend">
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

function DashboardCategoryChart({
  items,
  type = "bar",
  height = 260,
  valueLabel = "visitors",
  emptyText = "No data available yet.",
  indexAxis = "x",
}) {
  const canvasRef = React.useRef(null);
  const chartRef = React.useRef(null);

  React.useEffect(() => {
    if (!canvasRef.current || !window.Chart || !Array.isArray(items) || !items.length) {
      return undefined;
    }

    if (chartRef.current) {
      chartRef.current.destroy();
    }

    const labels = items.map((item) => item.label);
    const values = items.map((item) => toNonNegativeNumber(item.count));
    const palette = ["#0072BB", "#198754", "#F7A01A", "#ED1B2F", "#6F42C1", "#20C997", "#FD7E14", "#0DCAF0"];

    chartRef.current = new window.Chart(canvasRef.current, {
      type,
      data: {
        labels,
        datasets: [
          {
            label: valueLabel,
            data: values,
            backgroundColor:
              type === "doughnut"
                ? labels.map((_, index) => palette[index % palette.length])
                : labels.map((_, index) => `${palette[index % palette.length]}CC`),
            borderColor: labels.map((_, index) => palette[index % palette.length]),
            borderWidth: type === "doughnut" ? 1 : 0,
            borderRadius: type === "bar" ? 12 : 0,
            maxBarThickness: type === "bar" ? 28 : undefined,
            hoverOffset: type === "doughnut" ? 6 : 0,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis,
        cutout: type === "doughnut" ? "62%" : undefined,
        plugins: {
          legend:
            type === "doughnut"
                ? {
                  display: true,
                  position: "bottom",
                  labels: {
                    boxWidth: 12,
                    padding: 14,
                    usePointStyle: true,
                    pointStyle: "circle",
                  },
                }
              : { display: false },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const parsedValue =
                  typeof ctx.parsed === "object"
                    ? (indexAxis === "y" ? ctx.parsed.x : ctx.parsed.y)
                    : ctx.parsed;
                return ` ${ctx.label}: ${parsedValue} ${valueLabel}`;
              },
            },
          },
        },
        scales:
          type === "doughnut"
            ? {}
            : {
                x: {
                  beginAtZero: indexAxis === "y",
                  grid: { display: false },
                  border: { display: false },
                  ticks: {
                    font: { size: 11, weight: "600" },
                  },
                },
                y: {
                  beginAtZero: true,
                  grid: { color: "rgba(79, 93, 115, 0.18)" },
                  border: { display: false },
                  ticks: {
                    font: { size: 11, weight: "600" },
                    precision: 0,
                  },
                },
              },
      },
    });

    return () => chartRef.current?.destroy();
  }, [indexAxis, items, type, valueLabel]);

  if (!Array.isArray(items) || !items.length) {
    return <div className="dashboard-empty-state">{emptyText}</div>;
  }

  return (
    <div className="dashboard-line-chart" style={{ height }}>
      <canvas ref={canvasRef}></canvas>
    </div>
  );
}

// Main Dashboard Page
export default function Dashboard() {
  const todayInputValue = React.useMemo(() => getLocalDateInputValue(), []);
  const [data, setData] = React.useState(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState(false);
  const [exporting, setExporting] = React.useState(false);
  const [selectedFilter, setSelectedFilter] = React.useState("today");
  const [heatmapWeekStart, setHeatmapWeekStart] = React.useState(
    () => getWeekStartInputValue(todayInputValue)
  );
  const [distributionTab, setDistributionTab] = React.useState("gender");
  const [occupancyData, setOccupancyData] = React.useState(null);
  const [occupancyHistory, setOccupancyHistory] = React.useState([]);
  const [activeAlerts, setActiveAlerts] = React.useState([]);
  const [occupancyPanelError, setOccupancyPanelError] = React.useState("");
  const [dismissInFlightId, setDismissInFlightId] = React.useState(null);
  const [showSnapshotDetail, setShowSnapshotDetail] = React.useState(false);
  const latestRequestRef = React.useRef(0);
  const hasLoadedDataRef = React.useRef(false);
  const lastCapacityPopupKeyRef = React.useRef("");
  const lastCapacityPopupAtRef = React.useRef(0);
  const occupancyPanelRequestRef = React.useRef(null);

  React.useEffect(() => {
    hasLoadedDataRef.current = Boolean(data);
  }, [data]);

  React.useEffect(() => {
    const handleLayoutResize = () => {
      window.dispatchEvent(new Event("resize"));
      window.setTimeout(() => window.dispatchEvent(new Event("resize")), 340);
    };

    const observer = new MutationObserver((mutations) => {
      if (mutations.some((mutation) => mutation.attributeName === "class")) {
        handleLayoutResize();
      }
    });

    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  function buildDashboardQuery(filterKey, weekStart = heatmapWeekStart) {
    const query = new URLSearchParams({ filter: filterKey });
    query.set("heatmap_week_start", weekStart);
    return query.toString();
  }

  async function loadDashboardData({
    silent = false,
    filterKey = selectedFilter,
    weekStart = heatmapWeekStart,
  } = {}) {
    const requestId = latestRequestRef.current + 1;
    latestRequestRef.current = requestId;

    if (!silent) {
      setLoading(true);
    }

    try {
      const query = buildDashboardQuery(filterKey, weekStart);
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
    if (occupancyPanelRequestRef.current) {
      return occupancyPanelRequestRef.current;
    }

    const request = (async () => {
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
      } finally {
        occupancyPanelRequestRef.current = null;
      }
    })();

    occupancyPanelRequestRef.current = request;
    return request;
  }

  async function maybeShowCapacityPopup(payload) {
    const level = String(payload?.level || "").trim().toLowerCase();
    if (level !== "warning" && level !== "full") {
      return;
    }

    const occupancyCount = Number(payload?.occupancy_count ?? 0);
    const capacityLimit = Number(payload?.capacity_limit ?? 0);
    const occupancyRatio = Number(payload?.occupancy_ratio ?? 0);
    const percent = Number.isFinite(occupancyRatio)
      ? Math.max(0, Math.min(100, Math.round(occupancyRatio * 100)))
      : 0;
    const popupKey = `${level}:${occupancyCount}:${capacityLimit}:${percent}`;
    const now = Date.now();
    if (
      popupKey === lastCapacityPopupKeyRef.current &&
      (now - Number(lastCapacityPopupAtRef.current || 0)) < CAPACITY_ALERT_POPUP_COOLDOWN_MS
    ) {
      return;
    }
    lastCapacityPopupKeyRef.current = popupKey;
    lastCapacityPopupAtRef.current = now;

    let title = "Occupancy Warning";
    let text = `Current occupancy is ${occupancyCount}/${capacityLimit} (${percent}%).`;
    let icon = "warning";

    if (level === "full") {
      title = "Capacity Reached";
      icon = "error";
      if (capacityLimit > 0 && occupancyCount > capacityLimit) {
        text = `Current occupancy is ${occupancyCount}/${capacityLimit} (${percent}%). Capacity is exceeded.`;
      } else {
        text = `Current occupancy is ${occupancyCount}/${capacityLimit} (${percent}%). Entry flow should be monitored closely.`;
      }
    }

    await showAlert({
      icon,
      title,
      text,
      timer: CAPACITY_ALERT_TIMER_MS,
      showConfirmButton: false,
    });
  }

  function handleFilterChange(filterKey) {
    setSelectedFilter(filterKey);
  }

  function handlePreviousHeatmapWeek() {
    setHeatmapWeekStart((current) => shiftDateInput(current, -7));
  }

  function handleNextHeatmapWeek() {
    if (!data?.heatmap_can_go_next) return;
    setHeatmapWeekStart((current) => shiftDateInput(current, 7));
  }

  React.useEffect(() => {
    loadDashboardData({ filterKey: selectedFilter, weekStart: heatmapWeekStart });
    loadOccupancyPanel();

    const timer = window.setInterval(() => {
      loadDashboardData({
        silent: true,
        filterKey: selectedFilter,
        weekStart: heatmapWeekStart,
      });
      loadOccupancyPanel({ silent: true });
    }, 30000);

    return () => window.clearInterval(timer);
  }, [selectedFilter, heatmapWeekStart]);

  React.useEffect(() => {
    function handleAnalyticsUpdated() {
      loadDashboardData({
        silent: true,
        filterKey: selectedFilter,
        weekStart: heatmapWeekStart,
      });
      loadOccupancyPanel({ silent: true });
    }

    function handleCapacityAlert(payload) {
      loadOccupancyPanel({ silent: true });
      void maybeShowCapacityPopup(payload);
    }

    socket.connect();
    socket.on("analytics_updated", handleAnalyticsUpdated);
    socket.on("capacity_threshold_alert", handleCapacityAlert);
    return () => {
      socket.off("analytics_updated", handleAnalyticsUpdated);
      socket.off("capacity_threshold_alert", handleCapacityAlert);
      socket.disconnect();
    };
  }, [selectedFilter, heatmapWeekStart]);

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
  const totalEntries = data?.total_entries ?? 0;
  const totalExits = data?.total_exits ?? 0;
  const unrecognizedCount = data?.unrecognized_count ?? 0;
  const lowConfidenceCount = data?.low_confidence_count ?? 0;
  const dailyVisitors = data?.daily_visitors ?? [];
  const programDistrib = normalizeProgramDistribution(data?.program_distribution ?? []);
  const genderDistrib = normalizeGenderDistribution(data?.gender_distribution ?? []);
  const yearLevelDistrib = normalizeYearLevelDistribution(data?.year_level_distribution ?? []);
  const peakHoursRaw = data?.peak_hours ?? [];
  const peakHours = normalizeHourWindow(peakHoursRaw, PEAK_HOUR_START, PEAK_HOUR_END);
  const hourlyToday = normalizeHourWindow(peakHoursRaw, FULL_DAY_START, FULL_DAY_END);
  const recentEntryEvents = data?.recent_entries ?? [];
  const userTypeSummary = normalizeUserTypeDistribution(data?.user_type_distribution ?? []);
  const weeklyHeatmap = data?.weekly_heatmap ?? [];
  const peakPatternSummary = data?.peak_pattern_summary ?? {};
  const busiestDay = peakPatternSummary?.busiest_day ?? {};
  const busiestHour = peakPatternSummary?.busiest_hour ?? {};
  const viewMode = getDashboardViewMode(selectedFilter, data?.filter_days);
  const isTodayView = viewMode === "today";
  
  // Get filter label from selected filter, not from API response
  const selectedFilterOption = DASHBOARD_FILTER_OPTIONS.find(
    (opt) => opt.value === selectedFilter
  );
  const filterLabel = data?.filter_label ?? selectedFilterOption?.label ?? "Last 14 Days";
  const filterDateRange = formatRangeLabel(
    data?.filter_start_date,
    data?.filter_end_date
  );

  // Peak hour label for summary
  const peakHourMax = Math.max(...peakHours, 0);
  const peakHourIdx = peakHourMax > 0 ? peakHours.indexOf(peakHourMax) : -1;
  const peakHourLabel =
    peakHourIdx >= 0 ? formatDashboardHourLabel(peakHourIdx) : "N/A";
  const isSingleDateRange =
    data?.filter_start_date &&
    data?.filter_end_date &&
    data.filter_start_date === data.filter_end_date;
  const fullLogPath = isSingleDateRange
    ? `/entry-exit-logs?date=${data.filter_end_date}`
    : "/entry-exit-logs";

  async function handleExportClick() {
    if (!data) return;
    setExporting(true);
    try {
      await downloadDashboardExport(
        {
          ...data,
          live_occupancy: occupancyData,
          active_alerts: activeAlerts,
          occupancy_history: occupancyHistory,
        },
        selectedFilter
      );
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
  const heroTrafficLabels = isTodayView
    ? buildHourLabels(PEAK_HOUR_START, PEAK_HOUR_END).map((label) =>
        label.replace(" AM", "am").replace(" PM", "pm")
      )
    : dailyVisitors.map((item) => item.date);
  const heroTrafficValues = isTodayView ? peakHours : dailyVisitors.map((item) => item.count);
  const occupancyRemaining = Math.max(capacityLimit - occupancyCount, 0);
  const occupancyUpdatedAt = occupancyData?.updated_at || occupancyData?.timestamp_utc || "";
  const occupancyUpdatedLabel = occupancyUpdatedAt
    ? `Updated ${formatRelativeTime(occupancyUpdatedAt)}`
    : "Waiting for occupancy update";
  const occupancyStatusMessage = getOccupancyStatusMessage(
    occupancyCount,
    capacityLimit,
    isFull,
    capacityWarning
  );
  const peakSummaryStats = [
    {
      label: "Busiest hour",
      value: busiestHour?.label || "No visits",
      detail: `${toNonNegativeNumber(busiestHour?.count)} entr${toNonNegativeNumber(busiestHour?.count) === 1 ? "y" : "ies"}`,
      tone: "blue",
    },
    {
      label: "Busiest day",
      value: busiestDay?.label || "No visits",
      detail: `${toNonNegativeNumber(busiestDay?.count)} entr${toNonNegativeNumber(busiestDay?.count) === 1 ? "y" : "ies"}`,
      tone: "amber",
    },
    {
      label: "Average/day",
      value: peakPatternSummary?.average_daily_visits ?? 0,
      detail: `${toNonNegativeNumber(peakPatternSummary?.active_day_count)} active day${toNonNegativeNumber(peakPatternSummary?.active_day_count) === 1 ? "" : "s"}`,
      tone: "green",
    },
  ];
  const capacityTone = isFull ? "rose" : capacityWarning ? "amber" : "green";
  const netFlow = dailyEntries - dailyExits;
  const rangeNetFlow = totalEntries - totalExits;
  const rangeLabelLower = filterLabel.toLowerCase();
  const alertSummary = activeAlerts.length
    ? `${activeAlerts.length} active capacity alert${activeAlerts.length === 1 ? "" : "s"}`
    : "No active capacity alerts";
  const attentionItems = [
    {
      tone: "amber",
      label: `${lowConfidenceCount} low-confidence`,
      detail:
        lowConfidenceCount === 1
          ? `event should be reviewed in ${rangeLabelLower}`
          : `events should be reviewed in ${rangeLabelLower}`,
    },
    {
      tone: "rose",
      label: `${unrecognizedCount} unrecognized`,
      detail:
        unrecognizedCount === 1
          ? `face detected in ${rangeLabelLower}`
          : `faces detected in ${rangeLabelLower}`,
    },
    {
      tone: activeAlerts.length ? "blue" : "green",
      label: activeAlerts.length ? `${activeAlerts.length} active alerts` : "Flow is stable",
      detail: activeAlerts.length ? "capacity attention is still open" : "no unresolved capacity warning",
    },
  ];
  const distributionTabs = {
    gender: {
      key: "gender",
      label: "Gender",
      title: "Gender distribution",
      meta: `${genderDistrib.reduce((sum, item) => sum + item.count, 0)} visitors represented`,
      note: "This view summarizes how recognized visitors in the selected range are distributed by gender.",
      type: "doughnut",
      items: genderDistrib,
      emptyText: "No gender distribution is available for this range.",
    },
    program: {
      key: "program",
      label: "Program",
      title: "Program distribution",
      meta: `${programDistrib.length} program${programDistrib.length === 1 ? "" : "s"} represented`,
      note: "This view highlights which academic programs appear most often in the filtered dashboard range.",
      type: "bar",
      indexAxis: "y",
      items: programDistrib,
      emptyText: "No program distribution is available for this range.",
    },
    year: {
      key: "year",
      label: "Year level",
      title: "Year level distribution",
      meta: `${yearLevelDistrib.length} year-level bucket${yearLevelDistrib.length === 1 ? "" : "s"} represented`,
      note: "This view shows how recognized visitors in the selected range are spread across year levels.",
      type: "bar",
      items: yearLevelDistrib,
      emptyText: "No year-level distribution is available for this range.",
    },
  };
  const activeDistributionPanel = distributionTabs[distributionTab] || distributionTabs.gender;

  return (
    <section className="section dashboard dashboard-redesign">
      <div className="dashboard-shell">
        <div className="pagetitle dashboard-page-header">
          <div>
            <p className="dashboard-kicker">Real-time monitoring</p>
            <h1 className="mb-1">Dashboard</h1>
            <div className="text-muted small">
              Showing {filterLabel}
              {filterDateRange ? ` (${filterDateRange})` : ""}
            </div>
          </div>
          <div className="dashboard-header-actions">
            <div className="dashboard-filter-chips" role="group" aria-label="Dashboard filter">
              {DASHBOARD_FILTER_OPTIONS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  className={`dashboard-filter-chip${selectedFilter === option.value ? " is-active dashboard-accent-blue" : ""}`}
                  onClick={() => handleFilterChange(option.value)}
                  disabled={exporting}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleExportClick}
              disabled={!data || exporting}
            >
              <i className="bi bi-download me-1"></i>
              {exporting ? "Exporting..." : "Export"}
            </button>
          </div>
        </div>

        <div className="dashboard-overview-grid">
          <DashboardMetricCard
            title="Current occupancy"
            value={occupancyCount}
            meta={`Live count out of ${capacityLimit || 0} capacity`}
            accent="amber"
            badge={`${occupancyPercent}%`}
          />
          <DashboardMetricCard
            title={isTodayView ? "Entries today" : "Entries in range"}
            value={totalEntries}
            meta={
              rangeNetFlow >= 0
                ? `${rangeNetFlow} more entries than exits in ${rangeLabelLower}`
                : `${Math.abs(rangeNetFlow)} fewer than exits in ${rangeLabelLower}`
            }
            accent="blue"
          />
          <DashboardMetricCard
            title={isTodayView ? "Exits today" : "Exits in range"}
            value={totalExits}
            meta={
              totalEntries > 0
                ? `${Math.round((totalExits / Math.max(totalEntries, 1)) * 100)}% of entry volume`
                : `No exits recorded in ${rangeLabelLower}`
            }
            accent="green"
          />
          <DashboardMetricCard
            title="Unrecognized faces"
            value={unrecognizedCount}
            meta={
              lowConfidenceCount > 0
                ? `${lowConfidenceCount} low-confidence event${lowConfidenceCount === 1 ? "" : "s"}`
                : `No low-confidence events in ${rangeLabelLower}`
            }
            accent="rose"
          />
        </div>

        {occupancyPanelError ? (
          <div className="alert alert-danger mt-3 mb-0">
            <i className="bi bi-exclamation-triangle me-2"></i>
            {occupancyPanelError}
          </div>
        ) : null}

        <div className="dashboard-feature-grid">
          <article className="dashboard-panel dashboard-panel-hero">
            <div className="dashboard-panel-heading">
              <div>
                <p className="dashboard-panel-eyebrow">Occupancy & traffic</p>
                <h2 className="dashboard-panel-title">Live library floor</h2>
              </div>
              <span className={`dashboard-status-pill dashboard-accent-${capacityTone}`}>
                {occupancyStatusLabel}
              </span>
            </div>

            <div className="dashboard-occupancy-summary">
              <div>
                <div className="dashboard-occupancy-value-row">
                  <span className="dashboard-occupancy-value">{occupancyCount}</span>
                  <span className="dashboard-occupancy-capacity">/ {capacityLimit} capacity</span>
                </div>
                <p className="dashboard-occupancy-caption">
                  {isFull
                    ? "No seats available until exits are recorded."
                    : `${occupancyRemaining} slot${occupancyRemaining === 1 ? "" : "s"} available now.`}
                </p>
              </div>
              <div className="dashboard-occupancy-aside">
                <div className="dashboard-occupancy-percent">{occupancyPercent}%</div>
                <p className="dashboard-occupancy-caption">
                  {occupancyStatusMessage} {occupancyUpdatedLabel}.
                </p>
              </div>
            </div>

            {capacityLimit > 0 ? (
              <div
                className="dashboard-capacity-meter"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={occupancyPercent}
              >
                <div
                  className={`dashboard-capacity-fill dashboard-accent-${capacityTone}`}
                  style={{ width: `${occupancyPercent}%` }}
                ></div>
              </div>
            ) : (
              <div className="dashboard-empty-state">
                Capacity limit not configured.
              </div>
            )}
            <div className="dashboard-capacity-meta">
              <span>{dailyEntries} entries today</span>
              <span>{dailyExits} exits today</span>
              <span>{peakHourMax > 0 ? `Peak flow around ${peakHourLabel}` : "Waiting for more traffic data"}</span>
            </div>

            <div className="dashboard-subsection">
              <div className="dashboard-subsection-heading">
                <h3>{isTodayView ? "Entries per hour today" : "Traffic across the selected range"}</h3>
                <span>{filterLabel}</span>
              </div>
              <DashboardLineChart
                labels={heroTrafficLabels}
                values={heroTrafficValues}
                height={220}
                valueLabel={isTodayView ? "entries" : "visits"}
                lineColor="#0072BB"
                fillColor="rgba(0, 114, 187, 0.22)"
              />
            </div>
          </article>

          <aside className="dashboard-panel">
            <div className="dashboard-panel-heading">
              <div>
                <p className="dashboard-panel-eyebrow">Recognition feed</p>
                <h2 className="dashboard-panel-title">
                  {isTodayView ? "Recent activity" : "Latest activity in range"}
                </h2>
              </div>
              <span className="dashboard-panel-meta">
                {totalEntries + totalExits} movement event{totalEntries + totalExits === 1 ? "" : "s"} in {rangeLabelLower}
              </span>
            </div>
            <RecentEntriesList entries={recentEntryEvents} />
            <div className="dashboard-panel-footer">
              <span>Showing the most recent filtered entry and exit detections.</span>
              <Link to={fullLogPath}>View full log</Link>
            </div>
          </aside>
        </div>

        <div className="dashboard-insight-grid">
          <article className="dashboard-panel">
            <div className="dashboard-panel-heading">
              <div>
                <p className="dashboard-panel-eyebrow">Breakdown</p>
                <h2 className="dashboard-panel-title">
                  {isTodayView ? "Entries by program today" : "Visitors by program"}
                </h2>
              </div>
              <span className="dashboard-panel-meta">{filterLabel}</span>
            </div>

            <div className="dashboard-scroll-region">
              <DashboardProgressList
                items={programDistrib.map((item) => ({
                  ...item,
                  accent: "blue",
                }))}
                labelKey="label"
                valueKey="count"
                emptyText="No program distribution is available for this range."
              />
            </div>

            <div className="dashboard-section-divider"></div>

            <div className="dashboard-subsection-heading">
              <h3>User types in range</h3>
              <span>{uniqueVisitors} unique visitors in {rangeLabelLower}</span>
            </div>
            <DashboardProgressList
              items={userTypeSummary}
              labelKey="label"
              valueKey="count"
              accent="green"
              emptyText="No user type activity has been recorded for this range."
            />
          </article>

          <article className="dashboard-panel">
            <div className="dashboard-panel-heading">
              <div>
                <p className="dashboard-panel-eyebrow">Traffic patterns</p>
                <h2 className="dashboard-panel-title">Peak traffic patterns</h2>
              </div>
              <span className="dashboard-panel-meta">{getDashboardViewLabel(selectedFilter, data?.filter_days)}</span>
            </div>

            <div className="dashboard-pattern-summary-grid">
              {peakSummaryStats.map((item) => (
                <div key={item.label} className={`dashboard-pattern-stat dashboard-accent-${item.tone}`}>
                  <span className="dashboard-pattern-label">{item.label}</span>
                  <strong>{item.value}</strong>
                  <span>{item.detail}</span>
                </div>
              ))}
            </div>

            <WeeklyHeatmap
              data={weeklyHeatmap}
              weekLabel={data?.heatmap_week_label}
              canGoNext={Boolean(data?.heatmap_can_go_next)}
              onPreviousWeek={handlePreviousHeatmapWeek}
              onNextWeek={handleNextHeatmapWeek}
            />

            <div className="dashboard-section-divider"></div>

            <div className="dashboard-subsection-heading">
              <h3>Flags & attention</h3>
              <span>{alertSummary}</span>
            </div>
            <div className="dashboard-attention-list">
              {attentionItems.map((item) => (
                <div key={item.label} className="dashboard-attention-item">
                  <span className={`dashboard-attention-pill dashboard-accent-${item.tone}`}>
                    {item.label}
                  </span>
                  <span className="dashboard-attention-copy">{item.detail}</span>
                </div>
              ))}
            </div>
            <p className="dashboard-small-note">
              Average confidence for {rangeLabelLower}: <strong>{avgConfidence}%</strong>. {totalLogs} entry recognition log{totalLogs === 1 ? "" : "s"} and {totalStudents} registered profile{totalStudents === 1 ? "" : "s"} are included in this dashboard context.
            </p>
          </article>
        </div>

        <div className="dashboard-support-grid">
          <article className="dashboard-panel">
            <div className="dashboard-panel-heading">
              <div>
                <p className="dashboard-panel-eyebrow">Alerts</p>
                <h2 className="dashboard-panel-title">Capacity watchlist</h2>
              </div>
              <span className="dashboard-inline-badge">{activeAlerts.length}</span>
            </div>

            {activeAlerts.length ? (
              <div className="dashboard-alert-list">
                {activeAlerts.slice(0, 5).map((alert) => (
                  <div key={alert.id} className="dashboard-alert-item">
                    <div>
                      <div className="dashboard-alert-title">{alert.message || "Capacity alert"}</div>
                      <div className="dashboard-alert-meta">
                        {alert.occupancy_count}/{alert.capacity_limit} at {Math.round((alert.occupancy_ratio || 0) * 100)}% - {formatSnapshotTime(alert.created_at)}
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
                ))}
              </div>
            ) : (
              <div className="dashboard-empty-state">No active capacity alerts.</div>
            )}
          </article>

          <article className="dashboard-panel">
            <div className="dashboard-panel-heading">
              <div>
                <p className="dashboard-panel-eyebrow">Distribution</p>
                <h2 className="dashboard-panel-title">Visitor composition</h2>
              </div>
              <span className="dashboard-panel-meta">{filterLabel}</span>
            </div>
            <div className="dashboard-tabs" role="tablist" aria-label="Distribution charts">
              {Object.values(distributionTabs).map((tab) => (
                <button
                  key={tab.key}
                  type="button"
                  role="tab"
                  className={`dashboard-tab-button${distributionTab === tab.key ? " is-active" : ""}`}
                  aria-selected={distributionTab === tab.key}
                  onClick={() => setDistributionTab(tab.key)}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="dashboard-subsection dashboard-chart-section">
              <div className="dashboard-subsection-heading">
                <h3>{activeDistributionPanel.title}</h3>
                <span>{activeDistributionPanel.meta}</span>
              </div>
              <DashboardCategoryChart
                items={activeDistributionPanel.items}
                type={activeDistributionPanel.type}
                indexAxis={activeDistributionPanel.indexAxis || "x"}
                height={220}
                valueLabel="visitors"
                emptyText={activeDistributionPanel.emptyText}
              />
            </div>
            <p className="dashboard-small-note">{activeDistributionPanel.note}</p>

            <div className="dashboard-section-divider"></div>

            <button
              type="button"
              className="btn btn-sm btn-link dashboard-link-button"
              onClick={() => setShowSnapshotDetail(!showSnapshotDetail)}
              aria-expanded={showSnapshotDetail}
            >
              <i className={`bi bi-chevron-${showSnapshotDetail ? "up" : "down"} me-1`}></i>
              {showSnapshotDetail ? "Hide" : "Show"} snapshot timeline
            </button>

            {showSnapshotDetail && occupancyHistory.length ? (
              <div className="table-responsive mt-3">
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
                    {occupancyHistory.slice(-12).map((snapshot) => (
                      <tr key={`${snapshot.snapshot_timestamp}-${snapshot.occupancy_count}`}>
                        <td className="small">{formatSnapshotTimeWithDate(snapshot.snapshot_timestamp)}</td>
                        <td>
                          <strong>{snapshot.occupancy_count}</strong>
                          <span className="text-muted">/{snapshot.capacity_limit}</span>
                        </td>
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
            ) : showSnapshotDetail ? (
              <div className="dashboard-empty-state mt-3">No snapshots available yet for today.</div>
            ) : null}
          </article>
        </div>

        <div className="dashboard-note">
          <i className="bi bi-info-circle-fill"></i>
          <span>
            <strong>Dual-camera mode:</strong> Camera 1 records entries and Camera 2 records exits. Occupancy, alerts, and manual overrides update both flows in real time.
          </span>
        </div>
      </div>
    </section>
  );
}

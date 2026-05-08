import React from 'react';
import { getErrorMessage, showError, showSuccess } from '../alerts.js';
import { fetchJson } from '../api.js';
import { downloadFile } from '../downloads.js';

function currentYear() {
  return new Date().getFullYear();
}

function currentMonth() {
  return new Date().getMonth() + 1;
}

function fallbackMonthOptions() {
  return [
    'January',
    'February',
    'March',
    'April',
    'May',
    'June',
    'July',
    'August',
    'September',
    'October',
    'November',
    'December',
  ].map((label, index) => ({ value: index + 1, label }));
}

export default function MonthlyDailyVisitsPage() {
  const [data, setData] = React.useState({
    day_numbers: [],
    rows: [],
    overall_row: null,
    years: [],
    year: currentYear(),
    month: currentMonth(),
    month_label: fallbackMonthOptions()[currentMonth() - 1].label,
    month_options: fallbackMonthOptions(),
  });
  const [loading, setLoading] = React.useState(true);
  const [exporting, setExporting] = React.useState(false);
  const [search, setSearch] = React.useState('');
  const [pageSize, setPageSize] = React.useState('10');
  const [currentPage, setCurrentPage] = React.useState(1);
  const [selectedYear, setSelectedYear] = React.useState(String(currentYear()));
  const [selectedMonth, setSelectedMonth] = React.useState(String(currentMonth()));
  const [sortOrder, setSortOrder] = React.useState('most');
  const [showZeroVisits, setShowZeroVisits] = React.useState(true);

  const loadData = React.useCallback((year, month) => {
    setLoading(true);
    fetchJson(`/api/monthly-daily-visits?year=${encodeURIComponent(year)}&month=${encodeURIComponent(month)}`)
      .then((resp) => {
        const nextData = resp || {
          day_numbers: [],
          rows: [],
          overall_row: null,
          years: [currentYear()],
          year: currentYear(),
          month: currentMonth(),
          month_label: fallbackMonthOptions()[currentMonth() - 1].label,
          month_options: fallbackMonthOptions(),
        };
        setData(nextData);
        setSelectedYear(String(nextData.year || year));
        setSelectedMonth(String(nextData.month || month));
      })
      .catch(() => {
        setData({
          day_numbers: [],
          rows: [],
          overall_row: null,
          years: [currentYear()],
          year: currentYear(),
          month: currentMonth(),
          month_label: fallbackMonthOptions()[currentMonth() - 1].label,
          month_options: fallbackMonthOptions(),
        });
      })
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    loadData(selectedYear, selectedMonth);
  }, [loadData]);

  const rows = React.useMemo(() => {
    const sourceRows = Array.isArray(data.rows) ? data.rows : [];
    const dayCount = Array.isArray(data.day_numbers) ? data.day_numbers.length : 0;
    return sourceRows.map((row) => ({
      category: row.category,
      days: Array.isArray(row.days) ? row.days : Array(dayCount).fill(0),
      overall_total: Number(row.overall_total || 0),
    }));
  }, [data.day_numbers, data.rows]);

  const filtered = React.useMemo(() => {
    const searchValue = search.trim().toLowerCase();
    const nextRows = rows
      .filter((row) => (showZeroVisits ? true : row.overall_total > 0))
      .filter((row) => !searchValue || row.category.toLowerCase().includes(searchValue))
      .slice();

    nextRows.sort((a, b) => {
      if (sortOrder === 'least') {
        if (a.overall_total !== b.overall_total) return a.overall_total - b.overall_total;
        return a.category.localeCompare(b.category);
      }
      if (a.overall_total !== b.overall_total) return b.overall_total - a.overall_total;
      return a.category.localeCompare(b.category);
    });

    return nextRows;
  }, [rows, search, showZeroVisits, sortOrder]);

  const dayNumbers = React.useMemo(() => {
    if (Array.isArray(data.day_numbers) && data.day_numbers.length) {
      return data.day_numbers;
    }
    return [];
  }, [data.day_numbers]);

  const monthOptions = React.useMemo(() => {
    if (Array.isArray(data.month_options) && data.month_options.length) {
      return data.month_options;
    }
    return fallbackMonthOptions();
  }, [data.month_options]);

  const pageLimit = parseInt(pageSize, 10) || 10;
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageLimit));
  const safePage = Math.min(currentPage, totalPages);
  const startIndex = (safePage - 1) * pageLimit;
  const pageRows = filtered.slice(startIndex, startIndex + pageLimit);
  const exportHref = `/monthly-daily-visits/export?year=${encodeURIComponent(data.year || selectedYear)}&month=${encodeURIComponent(data.month || selectedMonth)}`;

  async function handleExportClick(event) {
    event.preventDefault();
    setExporting(true);

    try {
      await downloadFile(exportHref, `daily-library-users-${data.year || selectedYear}-${String(data.month || selectedMonth).padStart(2, '0')}.xlsx`);
      await showSuccess(
        'Export Complete',
        `Daily visits for ${data.month_label} ${data.year || selectedYear} were exported successfully.`
      );
    } catch (error) {
      await showError(
        'Export Failed',
        getErrorMessage(error, 'The monthly daily visits export could not be generated.')
      );
    } finally {
      setExporting(false);
    }
  }

  React.useEffect(() => {
    setCurrentPage(1);
  }, [search, pageSize, selectedYear, selectedMonth, sortOrder, showZeroVisits]);

  React.useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  function goToPage(page) {
    setCurrentPage(Math.max(1, Math.min(page, totalPages)));
  }

  function handleYearChange(event) {
    const nextYear = event.target.value;
    setSelectedYear(nextYear);
    loadData(nextYear, selectedMonth);
  }

  function handleMonthChange(event) {
    const nextMonth = event.target.value;
    setSelectedMonth(nextMonth);
    loadData(selectedYear, nextMonth);
  }

  const paginationItems = React.useMemo(() => {
    const items = [];
    const start = Math.max(1, safePage - 2);
    const end = Math.min(totalPages, start + 4);
    const adjustedStart = Math.max(1, end - 4);
    for (let page = adjustedStart; page <= end; page += 1) {
      items.push(page);
    }
    return items;
  }, [safePage, totalPages]);

  if (loading) {
    return (
      <div className="d-flex justify-content-center align-items-center" style={{ minHeight: '30vh' }}>
        <div className="spinner-border text-primary" role="status"></div>
      </div>
    );
  }

  return (
    <section className="section">
      <div className="pagetitle">
        <h1>Monthly Daily Visits</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item active">Monthly Daily Visits</li>
          </ol>
        </nav>
      </div>

      <div className="card mb-3">
        <div className="card-body">
          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mt-3">
            <div className="input-group" style={{ maxWidth: '520px', width: '100%' }}>
              <input
                type="text"
                className="form-control"
                placeholder="Search college, program, or office..."
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <button className="btn btn-danger" type="button">
                <i className="bi bi-search"></i>
              </button>
            </div>
            <div className="d-flex flex-nowrap gap-2 ms-lg-auto align-items-stretch justify-content-end">
              <select
                className="form-select"
                style={{ minWidth: '150px', height: '38px' }}
                value={selectedMonth}
                onChange={handleMonthChange}
              >
                {monthOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
              <select
                className="form-select"
                style={{ minWidth: '120px', height: '38px' }}
                value={selectedYear}
                onChange={handleYearChange}
              >
                {(data.years || []).map((year) => (
                  <option key={year} value={year}>
                    {year}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-outline-success d-flex align-items-center"
                style={{ height: '38px', whiteSpace: 'nowrap' }}
                onClick={handleExportClick}
                disabled={exporting}
              >
                <i className="bi bi-download me-1"></i>{exporting ? 'Exporting...' : 'Export'}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <h5 className="card-title">Daily Visit Matrix</h5>

          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
            <div className="small text-muted">
              Unique daily visits per college, program, or office for {data.month_label} {data.year}.
            </div>
            <div className="d-flex flex-wrap align-items-center gap-2">
              <select
                className="form-select"
                style={{ width: 'auto' }}
                value={sortOrder}
                onChange={(event) => setSortOrder(event.target.value)}
              >
                <option value="most">Most Visits</option>
                <option value="least">Least Visits</option>
              </select>
              <div
                className="d-flex align-items-center rounded border bg-light px-3"
                style={{ minHeight: '38px' }}
              >
                <div className="form-check form-switch m-0 d-flex align-items-center">
                  <input
                    id="showZeroDailyVisits"
                    className="form-check-input me-2"
                    type="checkbox"
                    checked={showZeroVisits}
                    onChange={(event) => setShowZeroVisits(event.target.checked)}
                  />
                  <label className="form-check-label small text-muted mb-0" htmlFor="showZeroDailyVisits">
                    Show zero visits
                  </label>
                </div>
              </div>
              <select
                className="form-select"
                style={{ width: 'auto' }}
                value={pageSize}
                onChange={(event) => setPageSize(event.target.value)}
              >
                <option value="10">10</option>
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
              </select>
            </div>
          </div>

          <div className="table-responsive">
            <table className="table table-hover align-middle" style={{ fontSize: '11px' }}>
              <thead>
                <tr>
                  <th className="text-center">College / Program / Office</th>
                  {dayNumbers.map((dayNumber) => (
                    <th key={dayNumber} className="text-center">
                      {dayNumber}
                    </th>
                  ))}
                  <th className="text-center">Overall Total</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.length ? (
                  <>
                    {pageRows.map((row) => (
                      <tr key={row.category}>
                        <td className="fw-medium text-center">{row.category}</td>
                        {(row.days || []).map((count, index) => (
                          <td key={`${row.category}-${index}`} className="text-center">
                            {count}
                          </td>
                        ))}
                        <td className="text-center fw-semibold">{row.overall_total}</td>
                      </tr>
                    ))}
                    {data.overall_row ? (
                      <tr className="table-light">
                        <td className="fw-bold text-center">{data.overall_row.category}</td>
                        {(data.overall_row.days || []).map((count, index) => (
                          <td key={`overall-${index}`} className="text-center fw-bold">
                            {count}
                          </td>
                        ))}
                        <td className="text-center fw-bold">{data.overall_row.overall_total}</td>
                      </tr>
                    ) : null}
                  </>
                ) : (
                  <tr>
                    <td colSpan={dayNumbers.length + 2} className="text-center text-muted py-4">
                      <i className="bi bi-table fs-3 d-block mb-2"></i>
                      No visit summary found for the selected month.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="small text-muted mt-2">
            {filtered.length ? startIndex + 1 : 0}-{Math.min(startIndex + pageRows.length, filtered.length)} of {filtered.length} categories shown
          </div>

          {totalPages > 1 ? (
            <nav aria-label="Monthly daily visits pagination" className="mt-3">
              <ul className="pagination pagination-sm mb-0 justify-content-end flex-wrap">
                <li className={`page-item ${safePage === 1 ? 'disabled' : ''}`}>
                  <button type="button" className="page-link" onClick={() => goToPage(safePage - 1)}>
                    Previous
                  </button>
                </li>
                {paginationItems.map((page) => (
                  <li key={page} className={`page-item ${page === safePage ? 'active' : ''}`}>
                    <button type="button" className="page-link" onClick={() => goToPage(page)}>
                      {page}
                    </button>
                  </li>
                ))}
                <li className={`page-item ${safePage === totalPages ? 'disabled' : ''}`}>
                  <button type="button" className="page-link" onClick={() => goToPage(safePage + 1)}>
                    Next
                  </button>
                </li>
              </ul>
            </nav>
          ) : null}
        </div>
      </div>
    </section>
  );
}

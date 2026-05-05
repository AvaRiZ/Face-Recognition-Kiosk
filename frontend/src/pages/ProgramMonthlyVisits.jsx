import React from 'react';
import { getErrorMessage, showError, showSuccess } from '../alerts.js';
import { fetchJson } from '../api.js';
import { downloadFile } from '../downloads.js';

function currentYear() {
  return new Date().getFullYear();
}

export default function ProgramMonthlyVisitsPage() {
  const [data, setData] = React.useState({ months: [], rows: [], overall_row: null, years: [], year: currentYear() });
  const [loading, setLoading] = React.useState(true);
  const [exporting, setExporting] = React.useState(false);
  const [search, setSearch] = React.useState('');
  const [pageSize, setPageSize] = React.useState('10');
  const [currentPage, setCurrentPage] = React.useState(1);
  const [selectedYear, setSelectedYear] = React.useState(String(currentYear()));
  const [sortOrder, setSortOrder] = React.useState('most');
  const [showZeroVisits, setShowZeroVisits] = React.useState(true);

  const loadData = React.useCallback((year) => {
    setLoading(true);
    fetchJson(`/api/program-monthly-visits?year=${encodeURIComponent(year)}`)
      .then((resp) => {
        setData(resp || { months: [], rows: [], overall_row: null, years: [], year: currentYear() });
        setSelectedYear(String(resp?.year || year));
      })
      .catch(() => {
        setData({ months: [], rows: [], overall_row: null, years: [currentYear()], year: currentYear() });
      })
      .finally(() => setLoading(false));
  }, []);

  React.useEffect(() => {
    loadData(selectedYear);
  }, [loadData]);

  const mergedRows = React.useMemo(() => {
    const sourceRows = Array.isArray(data.rows) ? data.rows : [];
    return sourceRows.map((row) => ({
      program: row.program,
      months: Array.isArray(row.months) ? row.months : Array(12).fill(0),
      overall_total: Number(row.overall_total || 0)
    }));
  }, [data.rows]);

  const filtered = React.useMemo(() => {
    const searchValue = search.trim().toLowerCase();
    const rows = mergedRows
      .filter((row) => (showZeroVisits ? true : row.overall_total > 0))
      .filter((row) => !searchValue || row.program.toLowerCase().includes(searchValue))
      .slice();

    rows.sort((a, b) => {
      if (sortOrder === 'least') {
        if (a.overall_total !== b.overall_total) return a.overall_total - b.overall_total;
        return a.program.localeCompare(b.program);
      }
      if (a.overall_total !== b.overall_total) return b.overall_total - a.overall_total;
      return a.program.localeCompare(b.program);
    });

    return rows;
  }, [mergedRows, search, showZeroVisits, sortOrder]);

  const monthLabels = React.useMemo(() => {
    if (Array.isArray(data.months) && data.months.length === 12) {
      return data.months;
    }
    return ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  }, [data.months]);

  const pageLimit = parseInt(pageSize, 10) || 10;
  const totalPages = Math.max(1, Math.ceil(filtered.length / pageLimit));
  const safePage = Math.min(currentPage, totalPages);
  const startIndex = (safePage - 1) * pageLimit;
  const pageRows = filtered.slice(startIndex, startIndex + pageLimit);
  const exportHref = `/program-monthly-visits/export?year=${encodeURIComponent(data.year || selectedYear)}`;

  async function handleExportClick(event) {
    event.preventDefault();
    setExporting(true);

    try {
      await downloadFile(exportHref, `program-monthly-visits-${data.year || selectedYear}.csv`);
      await showSuccess(
        'Export Complete',
        `Program monthly visits for ${data.year || selectedYear} were exported successfully.`
      );
    } catch (error) {
      await showError(
        'Export Failed',
        getErrorMessage(error, 'The monthly visits export could not be generated.')
      );
    } finally {
      setExporting(false);
    }
  }

  React.useEffect(() => {
    setCurrentPage(1);
  }, [search, pageSize, selectedYear, sortOrder, showZeroVisits]);

  React.useEffect(() => {
    if (currentPage > totalPages) {
      setCurrentPage(totalPages);
    }
  }, [currentPage, totalPages]);

  function goToPage(page) {
    setCurrentPage(Math.max(1, Math.min(page, totalPages)));
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
        <h1>Program Monthly Visits</h1>
        <nav>
          <ol className="breadcrumb mb-0">
            <li className="breadcrumb-item">
              <a href="/dashboard">Home</a>
            </li>
            <li className="breadcrumb-item active">Program Monthly Visits</li>
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
                placeholder="Search program..."
                value={search}
                onChange={(ev) => setSearch(ev.target.value)}
              />
              <button className="btn btn-danger" type="button">
                <i className="bi bi-search"></i>
              </button>
            </div>
            <div className="d-flex flex-nowrap gap-2 ms-lg-auto align-items-stretch justify-content-end">
              <select
                className="form-select"
                style={{ minWidth: '140px', height: '38px' }}
                value={String(data.year || selectedYear)}
                onChange={(ev) => loadData(ev.target.value)}
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
                <i className="bi bi-download me-1"></i>{exporting ? 'Exporting...' : 'Export CSV'}
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-body">
          <h5 className="card-title">Monthly Visit Matrix</h5>

          <div className="d-flex flex-wrap justify-content-between align-items-center gap-2 mb-3">
            <div className="small text-muted">
              Total visits per program in rows, grouped by month in columns for {data.year}.
            </div>
            <div className="d-flex flex-wrap align-items-center gap-2">
              <select
                className="form-select"
                style={{ width: 'auto' }}
                value={sortOrder}
                onChange={(ev) => setSortOrder(ev.target.value)}
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
                    id="showZeroVisits"
                    className="form-check-input me-2"
                    type="checkbox"
                    checked={showZeroVisits}
                    onChange={(ev) => setShowZeroVisits(ev.target.checked)}
                  />
                  <label className="form-check-label small text-muted mb-0" htmlFor="showZeroVisits">
                    Show zero visits
                  </label>
                </div>
              </div>
              <select
                className="form-select"
                style={{ width: 'auto' }}
                value={pageSize}
                onChange={(ev) => setPageSize(ev.target.value)}
              >
                <option value="10">10</option>
                <option value="25">25</option>
                <option value="50">50</option>
                <option value="100">100</option>
              </select>
            </div>
          </div>

          <div className="table-responsive">
            <table className="table table-hover align-middle">
              <thead>
                <tr>
                  <th>Program</th>
                  {monthLabels.map((month) => (
                    <th key={month} className="text-center">
                      {month}
                    </th>
                  ))}
                  <th className="text-center">Overall Total</th>
                </tr>
              </thead>
              <tbody>
                {pageRows.length ? (
                  <>
                    {pageRows.map((row) => (
                      <tr key={row.program}>
                        <td className="fw-medium">{row.program}</td>
                        {(row.months || []).map((count, idx) => (
                          <td key={`${row.program}-${idx}`} className="text-center">
                            {count}
                          </td>
                        ))}
                        <td className="text-center fw-semibold">{row.overall_total}</td>
                      </tr>
                    ))}
                    {data.overall_row ? (
                      <tr className="table-light">
                        <td className="fw-bold">{data.overall_row.program}</td>
                        {(data.overall_row.months || []).map((count, idx) => (
                          <td key={`overall-${idx}`} className="text-center fw-bold">
                            {count}
                          </td>
                        ))}
                        <td className="text-center fw-bold">{data.overall_row.overall_total}</td>
                      </tr>
                    ) : null}
                  </>
                ) : (
                  <tr>
                    <td colSpan={monthLabels.length + 2} className="text-center text-muted py-4">
                      <i className="bi bi-table fs-3 d-block mb-2"></i>
                      No visit summary found for the selected year.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>

          <div className="small text-muted mt-2">
            {filtered.length ? startIndex + 1 : 0}-{Math.min(startIndex + pageRows.length, filtered.length)} of {filtered.length} programs shown
          </div>

          {totalPages > 1 ? (
            <nav aria-label="Program monthly visits pagination" className="mt-3">
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

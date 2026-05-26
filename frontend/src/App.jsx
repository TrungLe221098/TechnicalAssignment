import { useState, useEffect, useCallback, useRef } from 'react'

const STATUS_COLORS = {
  'Active':      { bg: '#dcfce7', text: '#15803d', border: '#86efac' },
  'Not Started': { bg: '#fef9c3', text: '#a16207', border: '#fde047' },
  'Terminated':  { bg: '#fee2e2', text: '#b91c1c', border: '#fca5a5' },
}

const LOCATION_FLAGS = { VN: '🇻🇳', SGP: '🇸🇬', AUS: '🇦🇺' }

const COLUMNS = [
  { key: 'contact',    label: 'Contact' },
  { key: 'location',   label: 'Location' },
  { key: 'company',    label: 'Company' },
  { key: 'department', label: 'Department' },
  { key: 'position',   label: 'Position' },
]
const ALL_KEYS = COLUMNS.map(c => c.key)
const PAGE_SIZE = 10


// ── sub-components ────────────────────────────────────────────────────────────
function StatusBadge({ status }) {
  const c = STATUS_COLORS[status] || { bg: '#f3f4f6', text: '#374151', border: '#d1d5db' }
  return (
    <span style={{
      backgroundColor: c.bg, color: c.text, border: `1px solid ${c.border}`,
      padding: '2px 10px', borderRadius: '999px', fontSize: '12px', fontWeight: 600,
      whiteSpace: 'nowrap',
    }}>
      {status}
    </span>
  )
}

function Select({ label, value, options, onChange }) {
  return (
    <div className="filter-item">
      <label>{label}</label>
      <select value={value} onChange={e => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </div>
  )
}

function ColumnPicker({ visible, onChange }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  const toggle = (key) => {
    const next = new Set(visible)
    next.has(key) ? next.delete(key) : next.add(key)
    onChange(next)
  }

  return (
    <div className="col-picker-wrap" ref={ref}>
      <div className="filter-item">
        <label>Columns</label>
        <button className={`col-picker-btn${open ? ' active' : ''}`} onClick={() => setOpen(o => !o)}>
          <span className="col-picker-icon">⊞</span>
          {visible.size}/{COLUMNS.length} shown
          <span className="col-picker-caret">{open ? '▲' : '▼'}</span>
        </button>
      </div>

      {open && (
        <div className="col-picker-dropdown">
          <div className="col-picker-header">
            <span>Visible columns</span>
            <div className="col-picker-actions">
              <button onClick={() => onChange(new Set(ALL_KEYS))}>All</button>
              <button onClick={() => onChange(new Set())}>None</button>
            </div>
          </div>
          <div className="col-picker-list">
            {COLUMNS.map(col => (
              <label key={col.key} className="col-picker-item">
                <input type="checkbox" checked={visible.has(col.key)} onChange={() => toggle(col.key)} />
                <span>{col.label}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function Pagination({ pageIndex, hasNext, total, pageSize, itemCount, onPrev, onNext }) {
  if (total === 0) return null
  const from = pageIndex * pageSize + 1
  const to   = pageIndex * pageSize + itemCount

  return (
    <div className="pagination-bar">
      <span className="pagination-info">
        Showing <strong>{from}–{to}</strong> of <strong>{total}</strong> employees
      </span>
      <div className="pagination-controls">
        <button className="page-nav" disabled={pageIndex === 0} onClick={onPrev}>← Prev</button>
        <span className="page-btn active">{pageIndex + 1}</span>
        <button className="page-nav" disabled={!hasNext} onClick={onNext}>Next →</button>
      </div>
    </div>
  )
}

// ── main page ─────────────────────────────────────────────────────────────────
export default function App() {
  const [employees,   setEmployees]   = useState([])
  const [options,     setOptions]     = useState({ locations: [], companies: [], departments: [], positions: [], statuses: [] })
  const [filters,     setFilters]     = useState({ name: '', location: '', company: '', department: '', position: '', status: '' })
  const [visibleCols, setVisibleCols] = useState(new Set(ALL_KEYS))
  // cursorHistory[i] = cursor needed to fetch page i (null = first page)
  const [cursorHistory, setCursorHistory] = useState([null])
  const [pageIndex,   setPageIndex]   = useState(0)
  const [hasNext,     setHasNext]     = useState(false)
  const [total,       setTotal]       = useState(0)
  const [loading,     setLoading]     = useState(false)

  const show = (key) => visibleCols.has(key)

  useEffect(() => {
    fetch('/api/options')
      .then(r => { if (!r.ok) throw new Error(); return r.json() })
      .then(setOptions)
      .catch(() => {})
  }, [])

  const fetchEmployees = useCallback(async () => {
    setLoading(true)
    const params = new URLSearchParams({ page_size: PAGE_SIZE })
    Object.entries(filters).forEach(([k, v]) => { if (v) params.set(k, v) })
    const cursor = cursorHistory[pageIndex]
    if (cursor) params.set('cursor', cursor)
    try {
      const res = await fetch(`/api/employees?${params}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setEmployees(Array.isArray(data.items) ? data.items : [])
      setTotal(data.total ?? 0)
      setHasNext(data.has_next ?? false)
      // Store next_cursor for the following page if not already known
      if (data.has_next && data.next_cursor) {
        setCursorHistory(h => {
          if (h.length <= pageIndex + 1) return [...h, data.next_cursor]
          return h
        })
      }
    } catch {
      setEmployees([])
      setTotal(0)
      setHasNext(false)
    } finally {
      setLoading(false)
    }
  }, [filters, cursorHistory, pageIndex])

  useEffect(() => { fetchEmployees() }, [fetchEmployees])

  // Reset cursor state whenever a filter changes
  const set = (key) => (val) => {
    setFilters(f => ({ ...f, [key]: val }))
    setCursorHistory([null])
    setPageIndex(0)
  }

  const resetFilters = () => {
    setFilters({ name: '', location: '', company: '', department: '', position: '', status: '' })
    setCursorHistory([null])
    setPageIndex(0)
  }

  const hasFilters = Object.values(filters).some(v => v !== '')

  return (
    <div className="page">
      {/* ── Header ── */}
      <header className="header">
        <div className="header-inner">
          <div>
            <h1 className="header-title">HR Employee Management</h1>
            <p className="header-sub">Manage and search your global workforce</p>
          </div>
          <div className="header-badge">{total} employee{total !== 1 ? 's' : ''}</div>
        </div>
      </header>

      <main className="main">
        {/* ── Filter Bar ── */}
        <div className="filter-card">
          <div className="filter-row">
            <div className="filter-item filter-name">
              <label>Search by name</label>
              <input
                type="text"
                placeholder="First or last name..."
                value={filters.name}
                onChange={e => set('name')(e.target.value)}
              />
            </div>
            <Select label="Location"   value={filters.location}   options={options.locations}   onChange={set('location')} />
            <Select label="Company"    value={filters.company}    options={options.companies}   onChange={set('company')} />
            <Select label="Department" value={filters.department} options={options.departments} onChange={set('department')} />
            <Select label="Position"   value={filters.position}   options={options.positions}   onChange={set('position')} />
            <Select label="Status"     value={filters.status}     options={options.statuses}    onChange={set('status')} />
            <ColumnPicker visible={visibleCols} onChange={setVisibleCols} />
            {hasFilters && (
              <button className="reset-btn" onClick={resetFilters}>✕ Reset</button>
            )}
          </div>
        </div>

        {/* ── Table ── */}
        <div className="table-card">
          {loading ? (
            <div className="empty-state">
              <div className="spinner" />
              <p>Loading employees…</p>
            </div>
          ) : employees.length === 0 ? (
            <div className="empty-state">
              <p style={{ fontSize: 40 }}>🔍</p>
              <p>No employees match your filters.</p>
              {hasFilters && <button className="reset-btn" onClick={resetFilters}>Reset filters</button>}
            </div>
          ) : (
            <>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>First Name</th>
                      <th>Last Name</th>
                      {show('contact')    && <th>Contact</th>}
                      {show('location')   && <th>Location</th>}
                      {show('company')    && <th>Company</th>}
                      {show('department') && <th>Department</th>}
                      {show('position')   && <th>Position</th>}
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {employees.map((emp, i) => (
                      <tr key={emp.id}>
                        <td className="cell-num">{pageIndex * PAGE_SIZE + i + 1}</td>
                        <td className="cell-name">{emp.first_name}</td>
                        <td className="cell-name">{emp.last_name}</td>
                        {show('contact')    && <td>{emp.contact || '—'}</td>}
                        {show('location')   && (
                          <td>
                            <span className="location-tag">
                              {LOCATION_FLAGS[emp.location]} {emp.location}
                            </span>
                          </td>
                        )}
                        {show('company')    && <td>{emp.company}</td>}
                        {show('department') && <td>{emp.department}</td>}
                        {show('position')   && <td>{emp.position}</td>}
                        <td><StatusBadge status={emp.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <Pagination
                pageIndex={pageIndex}
                hasNext={hasNext}
                total={total}
                pageSize={PAGE_SIZE}
                itemCount={employees.length}
                onPrev={() => setPageIndex(i => i - 1)}
                onNext={() => setPageIndex(i => i + 1)}
              />
            </>
          )}
        </div>
      </main>
    </div>
  )
}

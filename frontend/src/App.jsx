import { useState, useCallback, useRef, useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import DependencyGraph from './DependencyGraph';
import './index.css';

const API_URL = 'http://localhost:8000';

function riskDotColor(score, maxScore) {
  if (maxScore === 0) return 'var(--text-faint)';
  const t = Math.sqrt(score / maxScore);
  if (t < 0.33) return 'var(--risk-low)';
  if (t < 0.66) return 'var(--risk-mid)';
  return 'var(--risk-high)';
}

function InfoTip({ text }) {
  const [pos, setPos] = useState(null);
  const iconRef = useRef(null);

  const show = () => {
    const rect = iconRef.current.getBoundingClientRect();
    const bubbleWidth = 220;
    let left = rect.left + rect.width / 2 - bubbleWidth / 2;
    left = Math.max(8, Math.min(left, window.innerWidth - bubbleWidth - 8));

    const spaceBelow = window.innerHeight - rect.bottom;
    const preferAbove = spaceBelow < 120; // rough min space a short tooltip needs below

    if (preferAbove) {
      setPos({ anchor: 'bottom', bottom: window.innerHeight - rect.top + 6, left });
    } else {
      setPos({ anchor: 'top', top: rect.bottom + 6, left });
    }
  };
  const hide = () => setPos(null);

  return (
    <span
      className="info-tip"
      ref={iconRef}
      tabIndex={0}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
    >
      <span className="info-tip-icon">i</span>
      {pos && (
        <span
          className="info-tip-bubble"
          style={
            pos.anchor === 'bottom'
              ? { bottom: pos.bottom, left: pos.left }
              : { top: pos.top, left: pos.left }
          }
        >
          {text}
        </span>
      )}
    </span>
  );
}

export default function App() {
  const [repoPath, setRepoPath] = useState('');
  const [excludeDirs, setExcludeDirs] = useState('');
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [selectedPath, setSelectedPath] = useState(null);
  const [explanation, setExplanation] = useState(null);
  const [explaining, setExplaining] = useState(false);
  const [explainError, setExplainError] = useState(null);
  const [provider, setProvider] = useState('ollama');
  const [filterQuery, setFilterQuery] = useState('');
  const [sidebarTab, setSidebarTab] = useState('hotspots');
  const [fileContents, setFileContents] = useState(null);
  const [contentsLoading, setContentsLoading] = useState(false);
  const [showAllDepends, setShowAllDepends] = useState(false);
  const [showAllDependedOn, setShowAllDependedOn] = useState(false);
  const [showAllContents, setShowAllContents] = useState(false);
  const [showAllViolations, setShowAllViolations] = useState(false);
  const [showAllReadingOrder, setShowAllReadingOrder] = useState(false);
  const [summary, setSummary] = useState(null);
  const [summarizing, setSummarizing] = useState(false);
  const [summarizeError, setSummarizeError] = useState(null);

  const handleAnalyze = async (e) => {
    e.preventDefault();
    if (!repoPath.trim()) return;
    setLoading(true);
    setError(null);
    setSelectedPath(null);
    setShowAllViolations(false);
    setShowAllReadingOrder(false);
    setFilterQuery('');
    try {
      const excludeList = excludeDirs
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      const res = await fetch(`${API_URL}/api/analyze`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: repoPath, exclude_dirs: excludeList }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || 'Analysis failed');
      setData(json);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const onSelect = useCallback((path) => {
    setSelectedPath(path);
    setExplanation(null);
    setExplainError(null);
  }, []);

  // Auto-fetch file contents (Option A) whenever selection changes — covers
  // every place a file gets selected (hotspot list, reading order, graph
  // clicks, detail-panel cross-links), not just onSelect.
  useEffect(() => {
    setFileContents(null);
    setSummary(null);
    setSummarizeError(null);
    setShowAllDepends(false);
    setShowAllDependedOn(false);
    setShowAllContents(false);
    if (!selectedPath || !data) return;
    setContentsLoading(true);
    fetch(`${API_URL}/api/file-contents`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ repo_path: data.repo, path: selectedPath }),
    })
      .then((res) => res.json())
      .then((json) => setFileContents(json))
      .catch(() => setFileContents(null))
      .finally(() => setContentsLoading(false));
  }, [selectedPath, data]);

  const maxScore = data ? Math.max(...data.hotspots.map((h) => h.score), 1) : 1;
  const selectedNode = data?.graph.nodes.find((n) => n.id === selectedPath);
  const selectedHotspot = data?.hotspots.find((h) => h.path === selectedPath);

  const connectedCoupling = data
    ? data.hidden_coupling.filter((c) => c.file_a === selectedPath || c.file_b === selectedPath)
    : [];

  const incoming = data
    ? data.graph.edges.filter((e) => e.target === selectedPath).map((e) => e.source)
    : [];
  const outgoing = data
    ? data.graph.edges.filter((e) => e.source === selectedPath).map((e) => e.target)
    : [];

  const handleSummarize = async () => {
    if (!selectedPath || !data) return;
    setSummarizing(true);
    setSummarizeError(null);
    try {
      const res = await fetch(`${API_URL}/api/summarize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repo_path: data.repo, path: selectedPath, provider }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || 'Summarize failed');
      setSummary({ provider: json.provider, text: json.summary, truncated: json.truncated });
    } catch (err) {
      setSummarizeError(err.message);
    } finally {
      setSummarizing(false);
    }
  };

  const handleExplain = async () => {
    if (!selectedNode) return;
    setExplaining(true);
    setExplainError(null);
    try {
      const res = await fetch(`${API_URL}/api/explain`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          path: selectedPath,
          complexity: selectedNode.complexity,
          churn: selectedNode.churn,
          hotspot_score: selectedHotspot ? selectedHotspot.score : 0,
          loc: selectedNode.loc,
          num_functions: selectedNode.num_functions,
          depends_on: outgoing,
          depended_on_by: incoming,
          hidden_coupling: connectedCoupling.map((c) => {
            const other = c.file_a === selectedPath ? c.file_b : c.file_a;
            return `${other} (${c.co_change_count}x)`;
          }),
          provider,
        }),
      });
      const json = await res.json();
      if (!res.ok) throw new Error(json.detail || 'Explanation failed');
      setExplanation({ provider: json.provider, text: json.explanation });
    } catch (err) {
      setExplainError(err.message);
    } finally {
      setExplaining(false);
    }
  };

  // Flatten grouped Contents (classes + their methods, then top-level
  // functions) into a single row list so we can apply one uniform
  // 5-row limit / expand behavior across the whole section.
  const contentsRows = (() => {
    if (!fileContents || fileContents.parse_error) return [];
    const rows = [];
    for (const cls of fileContents.classes) {
      rows.push({ type: 'class', key: `class-${cls.name}`, name: cls.name });
      for (const m of cls.methods) {
        rows.push({ type: 'method', key: `method-${cls.name}-${m.name}-${rows.length}`, name: m.name, complexity: m.complexity });
      }
    }
    for (const f of fileContents.top_level_functions) {
      rows.push({ type: 'function', key: `fn-${f.name}-${rows.length}`, name: f.name, complexity: f.complexity });
    }
    return rows;
  })();

  // Builds the full Markdown report client-side from data already loaded —
  // no backend round-trip needed, since /api/analyze already returned
  // everything this needs.
  function generateMarkdownReport(d) {
    const lines = [];
    lines.push(`# Engine Intelligence Report`);
    lines.push('');
    lines.push(`**Repository:** \`${d.repo}\``);
    lines.push(`**Generated:** ${new Date().toISOString()}`);
    lines.push('');
    lines.push(`## Summary`);
    lines.push('');
    lines.push(`| Metric | Value |`);
    lines.push(`|---|---|`);
    lines.push(`| Files analyzed | ${d.summary.num_files} |`);
    lines.push(`| Dependency edges | ${d.summary.num_edges} |`);
    lines.push(`| Hotspots | ${d.summary.num_hotspots} |`);
    lines.push(`| Boundary violations | ${d.summary.num_boundary_violations} |`);
    lines.push('');

    lines.push(`## Hotspots (complexity × churn)`);
    lines.push('');
    lines.push(`| File | Score | Complexity | Churn | LOC |`);
    lines.push(`|---|---|---|---|---|`);
    d.hotspots.forEach((h) => {
      lines.push(`| \`${h.path}\` | ${Math.round(h.score)} | ${h.complexity} | ${h.churn} | ${h.loc} |`);
    });
    lines.push('');

    if (d.hidden_coupling.length > 0) {
      lines.push(`## Hidden Coupling`);
      lines.push('');
      lines.push(`Files that repeatedly change together in the same commits despite having no import relationship.`);
      lines.push('');
      lines.push(`| File A | File B | Co-changes |`);
      lines.push(`|---|---|---|`);
      d.hidden_coupling.forEach((c) => {
        lines.push(`| \`${c.file_a}\` | \`${c.file_b}\` | ${c.co_change_count} |`);
      });
      lines.push('');
    }

    if (d.boundary_violations.length > 0) {
      lines.push(`## Boundary Violations`);
      lines.push('');
      lines.push(`| Module A | Module B | Detail |`);
      lines.push(`|---|---|---|`);
      d.boundary_violations.forEach((v) => {
        lines.push(`| ${v.module_a} | ${v.module_b} | ${v.detail} |`);
      });
      lines.push('');
    }

    lines.push(`## Suggested Reading Order`);
    lines.push('');
    d.reading_order.forEach((e) => {
      const cycleTag = e.in_cycle ? ' *(circular dependency cluster)*' : '';
      lines.push(`${e.position}. \`${e.path}\`${cycleTag} — ${e.reason}`);
    });
    lines.push('');

    lines.push('---');
    lines.push('*Generated by [Engine Intelligence](https://github.com/jaitotla/engine-intelligence).*');

    return lines.join('\n');
  }

  function downloadReport() {
    if (!data) return;
    const md = generateMarkdownReport(data);
    const blob = new Blob([md], { type: 'text/markdown' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const repoName = data.repo.split('/').filter(Boolean).pop() || 'report';
    a.download = `${repoName}-engine-intelligence-report.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  const filteredHotspots = data
    ? data.hotspots.filter((h) => h.path.toLowerCase().includes(filterQuery.toLowerCase()))
    : [];

  return (
    <div className="app">
      <div className="header">
        <h1>Engine Intelligence</h1>
        <form onSubmit={handleAnalyze} className="analyze-form">
          <input
            type="text"
            placeholder="/path/to/repo"
            value={repoPath}
            onChange={(e) => setRepoPath(e.target.value)}
          />
          <input
            type="text"
            placeholder="exclude paths (comma-separated)"
            value={excludeDirs}
            onChange={(e) => setExcludeDirs(e.target.value)}
            className="exclude-input"
          />
          <button type="submit" disabled={loading}>
            {loading ? 'Analyzing…' : 'Analyze'}
          </button>
        </form>
        {data && (
          <div className="stats-row">
            <div className="stat">
              <span className="value">{data.summary.num_files}</span>
              <span className="label">files</span>
            </div>
            <div className="stat">
              <span className="value">{data.summary.num_edges}</span>
              <span className="label">
                edges
                <InfoTip text="Import relationships found between files — how many files depend on other files." />
              </span>
            </div>
            <div className="stat">
              <span className="value">{data.summary.num_hotspots}</span>
              <span className="label">
                hotspots
                <InfoTip text="Files with a nonzero risk score: complicated code (high complexity) that's also been changed frequently (high churn)." />
              </span>
            </div>
            <div className="stat">
              <span className="value">{data.summary.num_boundary_violations}</span>
              <span className="label">
                boundary issues
                <InfoTip text="Cases where code in one architectural area directly depends on or changes alongside code in a different, supposedly separate area." />
              </span>
            </div>
          </div>
        )}
        {data && (
          <button className="export-btn" onClick={downloadReport} title="Download a Markdown report of this analysis">
            Export report
          </button>
        )}
      </div>

      <div className="body">
        <div className="sidebar">
          <div className="tab-switcher">
            <button
              className={sidebarTab === 'hotspots' ? 'tab-active' : ''}
              onClick={() => setSidebarTab('hotspots')}
            >
              Hotspots
            </button>
            <button
              className={sidebarTab === 'reading-order' ? 'tab-active' : ''}
              onClick={() => setSidebarTab('reading-order')}
            >
              Reading order
            </button>
          </div>

          {sidebarTab === 'hotspots' && (
            <>
              <div className="section-header">
                Hotspots (complexity × churn)
                <InfoTip text="Ranked by risk score = max function complexity × number of commits that touched the file." />
              </div>
              {data && (
                <div className="filter-box">
                  <input
                    type="text"
                    placeholder="Filter files…"
                    value={filterQuery}
                    onChange={(e) => setFilterQuery(e.target.value)}
                  />
                </div>
              )}
              {error && <div className="detail-empty error-message">{error}</div>}
              {!data && !error && (
                <div className="detail-empty">Enter a local path to a git repo above and click Analyze.</div>
              )}
              {data && (
                <div className="hotspot-list">
                  {filteredHotspots.map((h) => (
                    <div
                      key={h.path}
                      className={`hotspot-row ${h.path === selectedPath ? 'selected' : ''}`}
                      onClick={() => setSelectedPath(h.path)}
                    >
                      <span className="risk-dot" style={{ background: riskDotColor(h.score, maxScore) }} />
                      <span className="hotspot-path">{h.path}</span>
                      <span className="hotspot-score">{Math.round(h.score)}</span>
                    </div>
                  ))}
                  {filteredHotspots.length === 0 && (
                    <div className="detail-empty">No files match "{filterQuery}"</div>
                  )}
                </div>
              )}
              {data && data.boundary_violations.length > 0 && (
                <div className="violations-bar">
                  <div className="section-header" style={{ padding: 0, marginBottom: 8, border: 'none' }}>
                    Boundary violations ({data.boundary_violations.length})
                    <InfoTip text="Cross-module coupling: code that's supposed to live in a self-contained area but directly depends on, or changes alongside, code in a different area. Sorted by how often each pair crosses." />
                  </div>
                  {(showAllViolations ? data.boundary_violations : data.boundary_violations.slice(0, 5)).map((v, i) => (
                    <div key={i} className="violation-chip">
                      {v.module_a} ↔ {v.module_b}
                    </div>
                  ))}
                  {data.boundary_violations.length > 5 && (
                    <button className="show-more-btn" onClick={() => setShowAllViolations(!showAllViolations)}>
                      {showAllViolations ? 'Show less' : `Show ${data.boundary_violations.length - 5} more`}
                    </button>
                  )}
                </div>
              )}
            </>
          )}

          {sidebarTab === 'reading-order' && (
            <>
              <div className="section-header">
                Suggested reading order
                <InfoTip text="A topological order through the dependency graph — files with no unread prerequisites come first, ranked by how many other files depend on them and how risky they are. Files caught in a circular-dependency cluster are grouped at the end, since no single 'safe first' file exists among them." />
              </div>
              {!data && <div className="detail-empty">Analyze a repo to see a recommended reading order.</div>}
              {data && (
                <div className="reading-order-list">
                  {(showAllReadingOrder ? data.reading_order : data.reading_order.slice(0, 20)).map((e) => (
                    <div
                      key={e.path}
                      className={`reading-order-row ${e.path === selectedPath ? 'selected' : ''} ${e.in_cycle ? 'in-cycle' : ''}`}
                      onClick={() => setSelectedPath(e.path)}
                    >
                      <span className="reading-order-position">{e.position}</span>
                      <div className="reading-order-body">
                        <span className="hotspot-path">{e.path}</span>
                        <span className="reading-order-reason">{e.reason}</span>
                      </div>
                    </div>
                  ))}
                  {data.reading_order.length > 20 && (
                    <button className="show-more-btn" onClick={() => setShowAllReadingOrder(!showAllReadingOrder)}>
                      {showAllReadingOrder ? 'Show less' : `Show ${data.reading_order.length - 20} more`}
                    </button>
                  )}
                </div>
              )}
            </>
          )}
        </div>

        <div className="main">
          {data ? (
            <DependencyGraph
              nodes={data.graph.nodes}
              edges={data.graph.edges}
              selectedPath={selectedPath}
              onSelect={onSelect}
            />
          ) : (
            <div className="center-message">
              {loading ? 'Running AST parsing, git mining, and scoring…' : 'Dependency graph will render here.'}
            </div>
          )}
        </div>

        <div className="detail">
          {!selectedPath && <div className="detail-empty">Click a file in the hotspot list or graph to inspect it.</div>}
          {selectedPath && selectedNode && (
            <>
              <div className="detail-path">{selectedPath}</div>
              <div className="metric-grid">
                <div className="metric-cell">
                  <span className="value">{selectedHotspot ? Math.round(selectedHotspot.score) : 0}</span>
                  <span className="label">
                    hotspot score
                    <InfoTip text="Complexity × churn. Compare against other files' scores to judge relative risk." />
                  </span>
                </div>
                <div className="metric-cell">
                  <span className="value">{selectedNode.complexity}</span>
                  <span className="label">
                    max complexity
                    <InfoTip text="Cyclomatic complexity of this file's most complicated function." />
                  </span>
                </div>
                <div className="metric-cell">
                  <span className="value">{selectedNode.churn}</span>
                  <span className="label">
                    commits touched
                    <InfoTip text="How many separate commits modified this file." />
                  </span>
                </div>
                <div className="metric-cell">
                  <span className="value">{selectedNode.loc}</span>
                  <span className="label">lines of code</span>
                </div>
              </div>

              <div className="explain-block">
                <div className="explain-controls">
                  <select
                    className="provider-select"
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                  >
                    <option value="ollama">Ollama (local, free)</option>
                    <option value="anthropic">Claude API (paid)</option>
                  </select>
                  <button className="explain-btn" onClick={handleExplain} disabled={explaining}>
                    {explaining ? 'Thinking…' : 'Explain this file'}
                  </button>
                </div>
                {explainError && <div className="explain-error">{explainError}</div>}
                {explanation && (
                  <div className="explain-text">
                    <span className="explain-provider-tag">{explanation.provider}</span>
                    <ReactMarkdown>{explanation.text}</ReactMarkdown>
                  </div>
                )}
              </div>

              <div className="section-header">Depends on ({outgoing.length})</div>
              <div className="detail-list">
                {outgoing.length === 0 && <div className="detail-list-item">none</div>}
                {(showAllDepends ? outgoing : outgoing.slice(0, 5)).map((p) => (
                  <div key={p} className="detail-list-item" onClick={() => setSelectedPath(p)} style={{ cursor: 'pointer' }}>
                    {p}
                  </div>
                ))}
                {outgoing.length > 5 && (
                  <button className="show-more-btn" onClick={() => setShowAllDepends(!showAllDepends)}>
                    {showAllDepends ? 'Show less' : `Show ${outgoing.length - 5} more`}
                  </button>
                )}
              </div>

              <div className="section-header">Depended on by ({incoming.length})</div>
              <div className="detail-list">
                {incoming.length === 0 && <div className="detail-list-item">none</div>}
                {(showAllDependedOn ? incoming : incoming.slice(0, 5)).map((p) => (
                  <div key={p} className="detail-list-item" onClick={() => setSelectedPath(p)} style={{ cursor: 'pointer' }}>
                    {p}
                  </div>
                ))}
                {incoming.length > 5 && (
                  <button className="show-more-btn" onClick={() => setShowAllDependedOn(!showAllDependedOn)}>
                    {showAllDependedOn ? 'Show less' : `Show ${incoming.length - 5} more`}
                  </button>
                )}
              </div>

              <div className="section-header">
                Contents
                <InfoTip text="Functions and classes defined in this file, extracted directly from its structure — no AI involved, purely factual." />
              </div>
              <div className="detail-list">
                {contentsLoading && <div className="detail-list-item">loading…</div>}
                {!contentsLoading && fileContents?.parse_error && (
                  <div className="detail-list-item">could not parse: {fileContents.parse_error}</div>
                )}
                {!contentsLoading && fileContents && !fileContents.parse_error && (
                  <>
                    {contentsRows.length === 0 && (
                      <div className="detail-list-item">no functions or classes found</div>
                    )}
                    {(showAllContents ? contentsRows : contentsRows.slice(0, 5)).map((row) => {
                      if (row.type === 'class') {
                        return (
                          <div key={row.key} className="detail-list-item contents-class">class {row.name}</div>
                        );
                      }
                      return (
                        <div key={row.key} className={`detail-list-item ${row.type === 'method' ? 'contents-method' : ''}`}>
                          {row.name}()
                          {row.complexity > 5 && <span className="contents-complexity"> — complexity {row.complexity}</span>}
                        </div>
                      );
                    })}
                    {contentsRows.length > 5 && (
                      <button className="show-more-btn" onClick={() => setShowAllContents(!showAllContents)}>
                        {showAllContents ? 'Show less' : `Show ${contentsRows.length - 5} more`}
                      </button>
                    )}
                  </>
                )}
              </div>

              <div className="explain-block">
                <button className="explain-btn" onClick={handleSummarize} disabled={summarizing} style={{ width: '100%' }}>
                  {summarizing ? 'Reading file…' : 'Summarize this file'}
                </button>
                {summarizeError && <div className="explain-error">{summarizeError}</div>}
                {summary && (
                  <div className="explain-text">
                    <span className="explain-provider-tag">{summary.provider}</span>
                    {summary.truncated && <span className="truncated-tag">truncated</span>}
                    <ReactMarkdown>{summary.text}</ReactMarkdown>
                  </div>
                )}
              </div>

              {connectedCoupling.length > 0 && (
                <>
                  <div className="section-header">
                    Hidden coupling (change together, no import link)
                    <InfoTip text="Files that were modified in the same commits repeatedly, even though neither imports the other." />
                  </div>
                  <div className="detail-list">
                    {connectedCoupling.map((c, i) => {
                      const other = c.file_a === selectedPath ? c.file_b : c.file_a;
                      return (
                        <div key={i} className="detail-list-item" onClick={() => setSelectedPath(other)} style={{ cursor: 'pointer' }}>
                          {other} ({c.co_change_count}×)
                        </div>
                      );
                    })}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

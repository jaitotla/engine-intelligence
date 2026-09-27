import { useState, useCallback, useRef } from 'react';
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

  const handleAnalyze = async (e) => {
    e.preventDefault();
    if (!repoPath.trim()) return;
    setLoading(true);
    setError(null);
    setSelectedPath(null);
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
            placeholder="exclude folders (comma-separated)"
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
      </div>

      <div className="body">
        <div className="sidebar">
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
                Boundary violations
                <InfoTip text="Cross-module coupling: code that's supposed to live in a self-contained area but directly depends on, or changes alongside, code in a different area." />
              </div>
              {data.boundary_violations.map((v, i) => (
                <div key={i} className="violation-chip">
                  {v.module_a} ↔ {v.module_b}
                </div>
              ))}
            </div>
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
                {outgoing.map((p) => (
                  <div key={p} className="detail-list-item" onClick={() => setSelectedPath(p)} style={{ cursor: 'pointer' }}>
                    {p}
                  </div>
                ))}
              </div>

              <div className="section-header">Depended on by ({incoming.length})</div>
              <div className="detail-list">
                {incoming.length === 0 && <div className="detail-list-item">none</div>}
                {incoming.map((p) => (
                  <div key={p} className="detail-list-item" onClick={() => setSelectedPath(p)} style={{ cursor: 'pointer' }}>
                    {p}
                  </div>
                ))}
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

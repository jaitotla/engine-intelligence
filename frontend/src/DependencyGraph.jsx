import { useEffect, useRef } from 'react';
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from 'd3-force';
import { select } from 'd3-selection';
import { zoom } from 'd3-zoom';
import { drag } from 'd3-drag';

function riskColor(score, maxScore) {
  if (maxScore === 0) return 'var(--text-faint)';
  const t = Math.sqrt(score / maxScore); // sqrt so mid-range files aren't all crushed toward one end
  if (t < 0.33) return '#4fa382';
  if (t < 0.66) return '#c99a3d';
  return '#c4554a';
}

export default function DependencyGraph({ nodes, edges, selectedPath, onSelect }) {
  const svgRef = useRef(null);
  const containerRef = useRef(null);

  useEffect(() => {
    if (!nodes.length) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;
    const maxScore = Math.max(...nodes.map((n) => n.hotspot_score), 1);

    const simNodes = nodes.map((n) => ({ ...n }));
    const nodeById = new Map(simNodes.map((n) => [n.id, n]));
    const simEdges = edges
      .filter((e) => nodeById.has(e.source) && nodeById.has(e.target))
      .map((e) => ({ source: e.source, target: e.target }));

    const svg = select(svgRef.current);
    svg.selectAll('*').remove();

    const g = svg.append('g');

    svg.call(
      zoom()
        .scaleExtent([0.2, 4])
        .on('zoom', (event) => g.attr('transform', event.transform))
    );

    const link = g
      .append('g')
      .selectAll('line')
      .data(simEdges)
      .join('line')
      .attr('stroke', 'var(--border)')
      .attr('stroke-width', 1);

    const node = g
      .append('g')
      .selectAll('circle')
      .data(simNodes)
      .join('circle')
      .attr('r', (d) => 4 + Math.sqrt(d.loc || 1) * 0.35)
      .attr('fill', (d) => riskColor(d.hotspot_score, maxScore))
      .attr('stroke', (d) => (d.id === selectedPath ? '#e7e5df' : 'none'))
      .attr('stroke-width', 2)
      .style('cursor', 'pointer')
      .on('click', (_event, d) => onSelect(d.id))
      .call(
        drag()
          .on('start', (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on('drag', (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on('end', (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          })
      );

    node.append('title').text((d) => `${d.id}\nscore ${d.hotspot_score}  complexity ${d.complexity}  churn ${d.churn}`);

    const simulation = forceSimulation(simNodes)
      .force('link', forceLink(simEdges).id((d) => d.id).distance(60).strength(0.3))
      .force('charge', forceManyBody().strength(-120))
      .force('center', forceCenter(width / 2, height / 2))
      .force('collide', forceCollide().radius((d) => 6 + Math.sqrt(d.loc || 1) * 0.35));

    simulation.on('tick', () => {
      link
        .attr('x1', (d) => d.source.x)
        .attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x)
        .attr('y2', (d) => d.target.y);
      node.attr('cx', (d) => d.x).attr('cy', (d) => d.y);
    });

    return () => simulation.stop();
  }, [nodes, edges, selectedPath, onSelect]);

  if (!nodes.length) {
    return <div className="center-message">No graph data yet — analyze a repo to see its structure.</div>;
  }

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%' }}>
      <svg ref={svgRef} width="100%" height="100%" />
    </div>
  );
}

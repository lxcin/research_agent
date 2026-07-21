import { useRef, useEffect, useCallback, useState } from 'react';
import type { GraphNode, GraphEdge, GraphData, PaperTree } from '../types';

interface ForceGraphProps {
  data: GraphData;
  paperTrees: Record<string, PaperTree>;
  onClose?: () => void;
  onLoadPaperTree?: (paperId: string) => void;
}

const EDGE_COLORS: Record<string, string> = {
  supports: '#17A34A',
  contradicts: '#D9534F',
  extends: '#5E6AD2',
};

function getCSSVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

export default function ForceGraph({ data, paperTrees, onLoadPaperTree }: ForceGraphProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [treeView, setTreeView] = useState<PaperTree | null>(null);
  const animRef = useRef<number>(0);
  const stateRef = useRef({
    nodes: [] as GraphNode[],
    nodeMap: {} as Record<string, GraphNode>,
    edges: [] as GraphEdge[],
    transform: { x: 0, y: 0, k: 1 },
    hoveredNode: null as GraphNode | null,
    draggedNode: null as GraphNode | null,
    isPanning: false,
    panStart: { x: 0, y: 0 },
    dragStartPos: { x: 0, y: 0 },
    didDrag: false,
    alpha: 1,
    W: 0, H: 0,
  });

  const matchesSearch = useCallback((n: GraphNode) => {
    if (!searchQuery) return true;
    return n.label.toLowerCase().includes(searchQuery.toLowerCase());
  }, [searchQuery]);

  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;
    const ctx = canvas.getContext('2d')!;
    const dpr = window.devicePixelRatio || 1;
    const s = stateRef.current;

    function resize() {
      const rect = container!.getBoundingClientRect();
      s.W = rect.width; s.H = rect.height;
      canvas!.width = rect.width * dpr;
      canvas!.height = rect.height * dpr;
      canvas!.style.width = rect.width + 'px';
      canvas!.style.height = rect.height + 'px';
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    const nodes = data.nodes.map((n, i) => {
      const angle = (i / data.nodes.length) * Math.PI * 2;
      const r = 100 + Math.random() * 50;
      return {
        ...n,
        x: (s.W || 600) / 2 + Math.cos(angle) * r,
        y: (s.H || 450) / 2 + Math.sin(angle) * r,
        vx: 0, vy: 0, fx: null, fy: null,
      };
    });
    s.nodes = nodes;
    s.edges = data.edges;
    nodes.forEach(n => { s.nodeMap[n.id] = n; });

    resize();

    const ro = new ResizeObserver(() => resize());
    ro.observe(container);

    function screenToGraph(sx: number, sy: number) {
      return { x: (sx - s.transform.x) / s.transform.k, y: (sy - s.transform.y) / s.transform.k };
    }
    function findNodeAt(gx: number, gy: number): GraphNode | null {
      for (let i = s.nodes.length - 1; i >= 0; i--) {
        const n = s.nodes[i], dx = gx - n.x!, dy = gy - n.y!;
        if (dx * dx + dy * dy < 400) return n;
      }
      return null;
    }

    function simulate() {
      if (s.alpha < 0.001) return;
      s.alpha *= 0.995;
      for (let i = 0; i < s.nodes.length; i++) {
        for (let j = i + 1; j < s.nodes.length; j++) {
          const a = s.nodes[i], b = s.nodes[j];
          const dx = b.x! - a.x!, dy = b.y! - a.y!;
          const dist = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = -280 / (dist * dist);
          const fx = (dx / dist) * force, fy = (dy / dist) * force;
          a.vx! -= fx; a.vy! -= fy;
          b.vx! += fx; b.vy! += fy;
        }
      }
      s.edges.forEach(e => {
        const src = s.nodeMap[e.source], tgt = s.nodeMap[e.target];
        if (!src || !tgt) return;
        const dx = tgt.x! - src.x!, dy = tgt.y! - src.y!;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        const force = (dist - 130) * 0.004;
        const fx = (dx / dist) * force, fy = (dy / dist) * force;
        src.vx! += fx; src.vy! += fy;
        tgt.vx! -= fx; tgt.vy! -= fy;
      });
      s.nodes.forEach(n => {
        n.vx! += (s.W / 2 - n.x!) * 0.0004;
        n.vy! += (s.H / 2 - n.y!) * 0.0004;
      });
      s.nodes.forEach(n => {
        if (n.fx !== null) { n.x = n.fx; n.y = n.fy; n.vx = 0; n.vy = 0; return; }
        n.vx! *= 0.55; n.vy! *= 0.55;
        n.x! += n.vx!; n.y! += n.vy!;
      });
    }

    function drawEdge(e: GraphEdge) {
      const src = s.nodeMap[e.source], tgt = s.nodeMap[e.target];
      if (!src || !tgt) return;
      const dimmed = searchQuery && !matchesSearch(src) && !matchesSearch(tgt);
      ctx.beginPath();
      ctx.moveTo(src.x!, src.y!);
      ctx.lineTo(tgt.x!, tgt.y!);
      ctx.globalAlpha = dimmed ? 0.06 : 0.65;
      ctx.strokeStyle = EDGE_COLORS[e.type] || '#888';
      ctx.lineWidth = dimmed ? 0.5 : 1.5;
      if (e.type === 'extends') ctx.setLineDash([5, 3]);
      else ctx.setLineDash([]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.globalAlpha = 1;
    }

    function drawNode(n: GraphNode) {
      const matched = matchesSearch(n);
      const dimmed = searchQuery && !matched;
      const isHovered = s.hoveredNode === n;
      const isPaper = n.type === 'paper';
      const accent = getCSSVar('--accent') || '#5E6AD2';
      const bubbleUser = getCSSVar('--bubble-user') || '#e8e8ec';
      const border = getCSSVar('--border') || '#ccc';
      const fg = getCSSVar('--fg') || '#333';

      ctx.save();
      ctx.globalAlpha = dimmed ? 0.15 : 1;

      if (isPaper) {
        const sz = 28, x = n.x! - sz / 2, y = n.y! - sz / 2;
        ctx.beginPath();
        ctx.moveTo(x + 5, y);
        ctx.lineTo(x + sz - 5, y);
        ctx.quadraticCurveTo(x + sz, y, x + sz, y + 5);
        ctx.lineTo(x + sz, y + sz - 5);
        ctx.quadraticCurveTo(x + sz, y + sz, x + sz - 5, y + sz);
        ctx.lineTo(x + 5, y + sz);
        ctx.quadraticCurveTo(x, y + sz, x, y + sz - 5);
        ctx.lineTo(x, y + 5);
        ctx.quadraticCurveTo(x, y, x + 5, y);
        ctx.closePath();
        ctx.fillStyle = n.color || '#5E6AD2';
        ctx.fill();
        if (isHovered || (searchQuery && matched)) {
          ctx.strokeStyle = accent;
          ctx.lineWidth = 2.5;
          ctx.stroke();
        }
      } else {
        ctx.beginPath();
        ctx.arc(n.x!, n.y!, 14, 0, Math.PI * 2);
        ctx.fillStyle = bubbleUser;
        ctx.fill();
        ctx.strokeStyle = isHovered || (searchQuery && matched) ? accent : border;
        ctx.lineWidth = isHovered || (searchQuery && matched) ? 2.5 : 1.5;
        ctx.stroke();
      }

      const lines = n.label.split('\n');
      ctx.font = (isPaper ? '500 ' : '') + '11px -apple-system, BlinkMacSystemFont, "SF Pro Text", system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'top';
      ctx.fillStyle = dimmed ? 'rgba(128,128,128,0.2)' : fg;
      const labelY = n.y! + (isPaper ? 18 : 18);
      lines.forEach((line, i) => {
        ctx.fillText(line, n.x!, labelY + i * 13);
      });
      ctx.restore();
    }

    function render() {
      ctx.save();
      ctx.clearRect(0, 0, s.W, s.H);
      ctx.translate(s.transform.x, s.transform.y);
      ctx.scale(s.transform.k, s.transform.k);
      s.edges.forEach(drawEdge);
      s.nodes.forEach(drawNode);
      ctx.restore();
    }

    function tick() {
      simulate();
      render();
      animRef.current = requestAnimationFrame(tick);
    }

    const onMouseDown = (e: MouseEvent) => {
      const rect = canvas!.getBoundingClientRect();
      const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
      const gp = screenToGraph(sx, sy);
      const node = findNodeAt(gp.x, gp.y);
      s.dragStartPos.x = e.clientX; s.dragStartPos.y = e.clientY;
      s.didDrag = false;
      if (node) {
        s.draggedNode = node;
        node.fx = node.x; node.fy = node.y;
        s.alpha = Math.max(s.alpha, 0.3);
        canvas!.style.cursor = 'grabbing';
      } else {
        s.isPanning = true;
        s.panStart.x = e.clientX - s.transform.x;
        s.panStart.y = e.clientY - s.transform.y;
        canvas!.style.cursor = 'grabbing';
      }
    };

    const onMouseMove = (e: MouseEvent) => {
      const rect = canvas!.getBoundingClientRect();
      const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
      if (s.draggedNode) {
        if (Math.abs(e.clientX - s.dragStartPos.x) > 3 || Math.abs(e.clientY - s.dragStartPos.y) > 3) s.didDrag = true;
        const gp = screenToGraph(sx, sy);
        s.draggedNode.fx = gp.x; s.draggedNode.fy = gp.y;
        s.draggedNode.x = gp.x; s.draggedNode.y = gp.y;
        s.alpha = Math.max(s.alpha, 0.1);
      } else if (s.isPanning) {
        if (Math.abs(e.clientX - s.dragStartPos.x) > 3 || Math.abs(e.clientY - s.dragStartPos.y) > 3) s.didDrag = true;
        s.transform.x = e.clientX - s.panStart.x;
        s.transform.y = e.clientY - s.panStart.y;
      } else {
        const gp = screenToGraph(sx, sy);
        const node = findNodeAt(gp.x, gp.y);
        s.hoveredNode = node;
        canvas!.style.cursor = node ? 'pointer' : 'grab';
        const tt = tooltipRef.current;
        if (tt && node) {
          tt.querySelector('.tt-title')!.textContent = node.label.replace(/\n/g, ' ');
          tt.querySelector('.tt-meta')!.textContent = node.meta || '';
          const ttType = tt.querySelector('.tt-type')!;
          ttType.textContent = node.type === 'paper' ? '论文' : '观点';
          ttType.className = 'tt-type ' + node.type;
          let tx = sx + 14, ty = sy - 10;
          if (tx + 220 > s.W) tx = sx - 230;
          if (ty < 0) ty = 10;
          tt.style.left = tx + 'px';
          tt.style.top = ty + 'px';
          tt.classList.add('visible');
        } else if (tt) {
          tt.classList.remove('visible');
        }
      }
    };

    const onMouseUp = () => {
      if (s.draggedNode) {
        if (!s.didDrag && s.draggedNode.type === 'paper') {
          onLoadPaperTree?.(s.draggedNode.id);
          const tree = paperTrees[s.draggedNode.id];
          if (tree) setTreeView(tree);
        }
        s.draggedNode.fx = null; s.draggedNode.fy = null;
        s.draggedNode = null;
      }
      s.isPanning = false;
      canvas!.style.cursor = s.hoveredNode ? 'pointer' : 'grab';
    };

    const onMouseLeave = () => {
      s.hoveredNode = null;
      tooltipRef.current?.classList.remove('visible');
      if (s.draggedNode) { s.draggedNode.fx = null; s.draggedNode.fy = null; s.draggedNode = null; }
      s.isPanning = false;
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const rect = canvas!.getBoundingClientRect();
      const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
      const delta = -e.deltaY * 0.001;
      const newK = Math.max(0.3, Math.min(3, s.transform.k * (1 + delta)));
      const ratio = newK / s.transform.k;
      s.transform.x = sx - (sx - s.transform.x) * ratio;
      s.transform.y = sy - (sy - s.transform.y) * ratio;
      s.transform.k = newK;
    };

    canvas.addEventListener('mousedown', onMouseDown);
    canvas.addEventListener('mousemove', onMouseMove);
    canvas.addEventListener('mouseup', onMouseUp);
    canvas.addEventListener('mouseleave', onMouseLeave);
    canvas.addEventListener('wheel', onWheel, { passive: false });
    tick();

    return () => {
      cancelAnimationFrame(animRef.current);
      ro.disconnect();
      canvas.removeEventListener('mousedown', onMouseDown);
      canvas.removeEventListener('mousemove', onMouseMove);
      canvas.removeEventListener('mouseup', onMouseUp);
      canvas.removeEventListener('mouseleave', onMouseLeave);
      canvas.removeEventListener('wheel', onWheel);
    };
  }, [data, paperTrees, searchQuery, matchesSearch]);

  function renderTreeNode(node: TreeNode): string {
    const children = node.children?.map(c => renderTreeNode(c)).join('') || '';
    const cls = node.type === 'evidence' ? ' tree-node-item evidence' : ' tree-node-item';
    return `<div class="tree-claim"><div class="${cls}"><div class="tree-node-label">${escapeHtml(node.label)}</div></div>${children ? `<div class="tree-children">${children}</div>` : ''}</div>`;
  }

  return (
    <div className="graph-container" ref={containerRef}>
      <canvas className="graph-canvas-el" ref={canvasRef} />
      <div className="graph-legend">
        <div className="legend-item"><div className="legend-shape lg-paper" /><span>论文</span></div>
        <div className="legend-item"><div className="legend-shape lg-viewpoint" /><span>观点</span></div>
        <div style={{ height: 1, background: 'var(--border)', margin: '2px 0' }} />
        <div className="legend-item"><div className="legend-line" style={{ background: '#17A34A' }} /><span>支持</span></div>
        <div className="legend-item"><div className="legend-line" style={{ background: '#D9534F' }} /><span>反对</span></div>
        <div className="legend-item"><div className="legend-line" style={{ background: '#5E6AD2' }} /><span>扩展</span></div>
      </div>
      <div className="graph-search-bar">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="7" /><path d="M21 21l-4.35-4.35" /></svg>
        <input type="text" placeholder="搜索节点…" value={searchQuery} onChange={e => setSearchQuery(e.target.value)} />
      </div>
      <div className="graph-tooltip" ref={tooltipRef}>
        <div className="tt-title" />
        <div className="tt-meta" />
        <div className="tt-type" />
      </div>
      {treeView && (
        <div className="graph-tree-view active">
          <button className="graph-back-btn" onClick={() => setTreeView(null)}>
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M19 12H5" /><path d="M12 19l-7-7 7-7" /></svg>
            返回全局图谱
          </button>
          <h3 className="tree-paper-title">{treeView.title}</h3>
          <div dangerouslySetInnerHTML={{ __html: renderTreeNode(treeView.root) }} />
        </div>
      )}
    </div>
  );
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
import { useRef, useEffect, useCallback } from 'react';

interface FloatingWindowProps {
  isOpen: boolean;
  onToggle: () => void;
  title: string;
  children: React.ReactNode;
  defaultWidth?: number;
  defaultHeight?: number;
  defaultTop?: number;
  defaultLeft?: number;
  minWidth?: number;
  minHeight?: number;
}

export default function FloatingWindow({
  isOpen, onToggle, title, children,
  defaultWidth = 400, defaultHeight = 500,
  defaultTop = 80, defaultLeft = 80,
  minWidth = 300, minHeight = 350,
}: FloatingWindowProps) {
  const winRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ x0: 0, y0: 0, wx0: 0, wy0: 0, dragging: false });
  const resizeRef = useRef({ resizing: false, rx0: 0, ry0: 0, rw0: 0, rh0: 0 });

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest('.win-actions')) return;
    const win = winRef.current!;
    dragRef.current = { x0: e.clientX, y0: e.clientY, wx0: win.offsetLeft, wy0: win.offsetTop, dragging: true };
    e.preventDefault();
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = dragRef.current;
      if (!d.dragging) return;
      const win = winRef.current!;
      win.style.left = (d.wx0 + e.clientX - d.x0) + 'px';
      win.style.top = (d.wy0 + e.clientY - d.y0) + 'px';
    };
    const onUp = () => { dragRef.current.dragging = false; };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
  }, []);

  const onResizeDown = useCallback((e: React.MouseEvent) => {
    const win = winRef.current!;
    resizeRef.current = { resizing: true, rx0: e.clientX, ry0: e.clientY, rw0: win.offsetWidth, rh0: win.offsetHeight };
    e.preventDefault(); e.stopPropagation();
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const r = resizeRef.current;
      if (!r.resizing) return;
      const win = winRef.current!;
      win.style.width = Math.max(minWidth, r.rw0 + e.clientX - r.rx0) + 'px';
      win.style.height = Math.max(minHeight, r.rh0 + e.clientY - r.ry0) + 'px';
    };
    const onUp = () => { resizeRef.current.resizing = false; };
    document.addEventListener('mousemove', onMove);
    document.addEventListener('mouseup', onUp);
    return () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
    };
  }, [minWidth, minHeight]);

  return (
    <div
      ref={winRef}
      className={`floating-window${isOpen ? ' open' : ''}`}
      style={{ width: defaultWidth, height: defaultHeight, top: defaultTop, left: defaultLeft }}
    >
      <div className="win-header" onMouseDown={onMouseDown}>
        <span className="win-title">{title}</span>
        <div className="win-actions">
          <button onClick={onToggle} title="折叠">—</button>
          <button onClick={onToggle} title="关闭">✕</button>
        </div>
      </div>
      <div className="win-body" style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {children}
      </div>
      <div className="resize-handle" onMouseDown={onResizeDown}>
        <svg viewBox="0 0 10 10" width="10" height="10"><path d="M1 9l8-8M5 9l4-4M9 9l0-0" stroke="currentColor" strokeWidth="1.5" fill="none" /></svg>
      </div>
    </div>
  );
}
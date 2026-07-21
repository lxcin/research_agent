import { useState, useEffect, useRef, useCallback } from 'react';

interface FileEntry {
    name: string;
    size: number;
}

interface WorkspaceSidebarProps {
    projectId: string;
    isOpen: boolean;
    onToggle: () => void;
    refreshKey?: number;
}

function formatSize(bytes: number): string {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

const EXT_ICONS: Record<string, string> = {
    py: '🐍', csv: '📊', html: '🌐', png: '🖼', svg: '🎨',
    json: '📋', md: '📝', txt: '📄', pdf: '📕', js: '💛',
};

export default function WorkspaceSidebar({ projectId, isOpen, onToggle, refreshKey }: WorkspaceSidebarProps) {
    const [files, setFiles] = useState<FileEntry[]>([]);
    const [dir, setDir] = useState('');
    const [preview, setPreview] = useState<{ name: string; content: string; ext: string } | null>(null);
    const [width, setWidth] = useState(280);
    const resizeRef = useRef<HTMLDivElement>(null);
    const dragging = useRef(false);

    const loadFiles = useCallback(() => {
        fetch(`/api/workspace/${projectId}`)
            .then(r => r.json())
            .then(data => { setFiles(data.files || []); setDir(data.dir || ''); })
            .catch(() => {});
    }, [projectId]);

    useEffect(() => { if (isOpen) loadFiles(); }, [loadFiles, isOpen, refreshKey]);

    // Resize drag
    useEffect(() => {
        const onMouseMove = (e: MouseEvent) => {
            if (!dragging.current) return;
            const w = window.innerWidth - e.clientX;
            if (w > 180 && w < 600) setWidth(w);
        };
        const onMouseUp = () => { dragging.current = false; };
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        return () => {
            document.removeEventListener('mousemove', onMouseMove);
            document.removeEventListener('mouseup', onMouseUp);
        };
    }, []);

    const handlePreview = (filename: string) => {
        const ext = filename.split('.').pop() || '';
        const textExts = ['py', 'md', 'txt', 'csv', 'json', 'html', 'js', 'css', 'yml', 'yaml'];
        if (!textExts.includes(ext)) return;
        fetch(`/api/project-files/${projectId}/${filename}`)
            .then(r => r.text())
            .then(text => setPreview({ name: filename, content: text, ext }))
            .catch(() => {});
    };

    if (!isOpen) {
        return (
            <div className="ws-toggle-btn" onClick={onToggle} title="工作区">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" width="16" height="16"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
            </div>
        );
    }

    return (
        <div className="ws-sidebar" style={{ width }}>
            <div className="ws-resize-handle" ref={resizeRef}
                 onMouseDown={() => { dragging.current = true; }} />
            <div className="ws-sidebar-inner">
                <div className="ws-header">
                    <span>工作区</span>
                    <div className="ws-header-btns">
                        <button onClick={loadFiles} title="刷新">
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14"><path d="M21 2v6h-6M3 12a9 9 0 0115-6.7L21 8M3 22v-6h6M21 12a9 9 0 01-15 6.7L3 16"/></svg>
                        </button>
                        <button onClick={onToggle} title="关闭">×</button>
                    </div>
                </div>
                <div className="ws-dir">{dir.split(/[\\\/]/).slice(-2).join('/') || '加载中...'}</div>
                <div className="ws-list">
                    {files.length === 0 ? (
                        <div className="ws-empty">暂无文件</div>
                    ) : (
                        files.map(f => {
                            const ext = f.name.split('.').pop() || '';
                            const icon = EXT_ICONS[ext] || '📄';
                            return (
                                <div key={f.name} className="ws-file" onClick={() => handlePreview(f.name)}
                                     title={`${f.name} (${formatSize(f.size)})`}>
                                    <span className="ws-ficon">{icon}</span>
                                    <span className="ws-fname">{f.name}</span>
                                    <span className="ws-fsize">{formatSize(f.size)}</span>
                                </div>
                            );
                        })
                    )}
                </div>
                {preview && (
                    <div className="ws-preview-panel">
                        <div className="ws-preview-header">
                            <span>{preview.name}</span>
                            <button onClick={() => setPreview(null)}>×</button>
                        </div>
                        <pre className="ws-preview-content">{preview.content}</pre>
                    </div>
                )}
            </div>
        </div>
    );
}
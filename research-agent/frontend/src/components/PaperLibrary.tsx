import { useState, useEffect, useCallback } from 'react';

interface Paper {
    id: string;
    title: string;
    year: number;
    authors: string[];
    doi: string;
    citation_count: number;
    abstract: string;
    source_score: number;
}

interface PaperLibraryProps {
    workspacePath?: string;
}

export default function PaperLibrary({ workspacePath }: PaperLibraryProps) {
    const [papers, setPapers] = useState<Paper[]>([]);
    const [globalPapers, setGlobalPapers] = useState<Paper[]>([]);
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState<Paper | null>(null);
    const [showGlobal, setShowGlobal] = useState(false);

    const loadPapers = useCallback(() => {
        if (workspacePath) {
            fetch(`/api/workspaces/papers?dir=${encodeURIComponent(workspacePath)}`)
                .then(r => r.json())
                .then(setPapers)
                .catch(() => {});
        } else {
            fetch('/api/papers')
                .then(r => r.json())
                .then(setPapers)
                .catch(() => {});
        }
    }, [workspacePath]);

    const loadGlobalPapers = useCallback(() => {
        fetch('/api/papers')
            .then(r => r.json())
            .then(data => { setGlobalPapers(data); setPapers(data); })
            .catch(() => {});
    }, []);

    useEffect(() => { loadPapers(); }, [loadPapers]);

    const handleDelete = useCallback((id: string) => {
        if (!confirm('确定删除这篇论文？')) return;
        fetch(`/api/papers/${id}`, { method: 'DELETE' })
            .then(r => { if (r.ok) loadPapers(); });
    }, [loadPapers]);

    const handleToggleView = useCallback(() => {
        if (showGlobal) {
            setShowGlobal(false);
            loadPapers();
        } else {
            setShowGlobal(true);
            if (globalPapers.length === 0) {
                loadGlobalPapers();
            } else {
                setPapers(globalPapers);
            }
        }
    }, [showGlobal, globalPapers, loadGlobalPapers, loadPapers]);

    const displayPapers = showGlobal ? globalPapers : papers;
    const filtered = displayPapers.filter(p =>
        !search || p.title.toLowerCase().includes(search.toLowerCase()) ||
        p.authors.some(a => a.toLowerCase().includes(search.toLowerCase()))
    );

    const wsName = workspacePath ? workspacePath.replace(/\\/g, '/').split('/').pop() || workspacePath : null;

    return (
        <div className="paper-library">
            <div className="pl-header">
                <h3>{wsName ? `${wsName}` : '论文库'} ({filtered.length})</h3>
                <div className="pl-header-actions">
                    {workspacePath && (
                        <button className={`pl-view-toggle${showGlobal ? ' active' : ''}`} onClick={handleToggleView} title={showGlobal ? '切换到工作区论文' : '查看全部论文'}>
                            {showGlobal ? '全部论文' : '工作区'}
                        </button>
                    )}
                    <button className="pl-refresh-btn" onClick={loadPapers} title="刷新">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                            <path d="M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6M21 12a9 9 0 0 1-15 6.7L3 16"/>
                        </svg>
                    </button>
                </div>
            </div>

            <div className="pl-search">
                <input type="text" placeholder="搜索论文..." value={search}
                       onChange={e => setSearch(e.target.value)} />
            </div>

            <div className="pl-list">
                {filtered.length === 0 ? (
                    <div className="pl-empty">暂无论文，开始搜索 arXiv 吧</div>
                ) : (
                    filtered.map(p => (
                        <div key={p.id} className={`pl-item${selected?.id === p.id ? ' pl-item-active' : ''}`}
                             onClick={() => setSelected(selected?.id === p.id ? null : p)}>
                            <div className="pl-item-top">
                                <span className="pl-item-title">{p.title}</span>
                                <span className="pl-item-year">{p.year || 'N/A'}</span>
                            </div>
                            <div className="pl-item-authors">{p.authors.slice(0, 3).join(', ')}</div>
                            <div className="pl-item-actions">
                                <span className="pl-item-score">评分 {p.source_score}</span>
                                <button className="pl-delete-btn" onClick={e => { e.stopPropagation(); handleDelete(p.id); }}
                                        title="删除">✕</button>
                            </div>

                            {selected?.id === p.id && (
                                <div className="pl-item-detail">
                                    {p.doi && <div className="pl-detail-row"><b>arXiv:</b> {p.doi.replace('arxiv:', '')}</div>}
                                    <div className="pl-detail-row"><b>引用:</b> {p.citation_count}</div>
                                    <p className="pl-detail-abstract">{p.abstract || '无摘要'}</p>
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}
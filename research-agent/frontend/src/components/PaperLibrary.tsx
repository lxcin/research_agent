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

export default function PaperLibrary() {
    const [papers, setPapers] = useState<Paper[]>([]);
    const [search, setSearch] = useState('');
    const [selected, setSelected] = useState<Paper | null>(null);

    const loadPapers = useCallback(() => {
        fetch('/api/papers')
            .then(r => r.json())
            .then(setPapers)
            .catch(() => {});
    }, []);

    useEffect(() => { loadPapers(); }, [loadPapers]);

    const handleDelete = useCallback((id: string) => {
        if (!confirm('确定删除这篇论文？')) return;
        fetch(`/api/papers/${id}`, { method: 'DELETE' })
            .then(r => { if (r.ok) loadPapers(); });
    }, [loadPapers]);

    const filtered = papers.filter(p =>
        !search || p.title.toLowerCase().includes(search.toLowerCase()) ||
        p.authors.some(a => a.toLowerCase().includes(search.toLowerCase()))
    );

    return (
        <div className="paper-library">
            <div className="pl-header">
                <h3>论文库 ({papers.length})</h3>
                <button className="pl-refresh-btn" onClick={loadPapers} title="刷新">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                        <path d="M21 2v6h-6M3 12a9 9 0 0 1 15-6.7L21 8M3 22v-6h6M21 12a9 9 0 0 1-15 6.7L3 16"/>
                    </svg>
                </button>
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
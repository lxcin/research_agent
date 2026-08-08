import { useState, useCallback } from 'react';

interface PaperDetail {
    id: string;
    title: string;
    year: number;
    authors: string[];
    doi: string;
    citation_count: number;
    abstract: string;
    source_score: number;
}

interface CitationCardProps {
    paperId: string;
    workspacePath?: string;
    onReadPaper?: (paperId: string) => void;
}

export default function CitationCard({ paperId, workspacePath, onReadPaper }: CitationCardProps) {
    const [expanded, setExpanded] = useState(false);
    const [paper, setPaper] = useState<PaperDetail | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(false);

    const fetchPaper = useCallback(async () => {
        if (paper || loading) return;
        setLoading(true);
        try {
            const params = new URLSearchParams();
            if (workspacePath) params.set('dir', workspacePath);
            params.set('paper_id', paperId);
            const res = await fetch(`/api/workspaces/paper?${params.toString()}`);
            if (res.ok) {
                const data = await res.json();
                setPaper(data);
            } else {
                setError(true);
            }
        } catch {
            setError(true);
        } finally {
            setLoading(false);
        }
    }, [paperId, workspacePath, paper, loading]);

    const handleToggle = useCallback(() => {
        if (!expanded) {
            fetchPaper();
            setExpanded(true);
        } else {
            setExpanded(false);
        }
    }, [expanded, fetchPaper]);

    const shortId = paperId.length > 16 ? paperId.slice(0, 14) + '...' : paperId;

    return (
        <span className="citation-card">
            <span className="citation-card-badge" onClick={handleToggle} title={paperId}>
                paper:{shortId}
                <span className="citation-card-caret">{expanded ? ' ▾' : ' ▸'}</span>
            </span>
            {expanded && (
                <div className="citation-card-body">
                    {loading && <div className="citation-card-loading">加载中...</div>}
                    {error && <div className="citation-card-loading">未找到论文: {paperId}</div>}
                    {paper && (
                        <>
                            <div className="citation-card-title">{paper.title}</div>
                            <div className="citation-card-meta">
                                {(paper.authors || []).join(', ')}
                                {paper.year ? ` (${paper.year})` : ''}
                            </div>
                            {paper.doi && (
                                <div className="citation-card-doi">{paper.doi}</div>
                            )}
                            {paper.abstract && (
                                <p className="citation-card-abstract">{paper.abstract}</p>
                            )}
                            <button
                                className="citation-card-read-btn"
                                onClick={() => onReadPaper?.(paperId)}
                            >
                                读全文
                            </button>
                        </>
                    )}
                </div>
            )}
        </span>
    );
}

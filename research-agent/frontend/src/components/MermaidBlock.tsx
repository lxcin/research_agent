import { useEffect, useRef, useState } from 'react';

interface MermaidBlockProps {
    chart: string;
}

export default function MermaidBlock({ chart }: MermaidBlockProps) {
    const ref = useRef<HTMLDivElement>(null);
    const [error, setError] = useState('');

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const mermaid = (await import('mermaid')).default;
                mermaid.initialize({ startOnLoad: false, theme: 'base',
                    themeVariables: { primaryColor: '#5E6AD2', primaryTextColor: '#e0e0e0',
                        lineColor: '#5E6AD2', fontSize: '14px' } });
                const id = `mermaid-${Date.now()}`;
                const { svg } = await mermaid.render(id, chart);
                if (!cancelled && ref.current) ref.current.innerHTML = svg;
            } catch (e: any) {
                if (!cancelled) setError(e.message);
            }
        })();
        return () => { cancelled = true; };
    }, [chart]);

    if (error) return <pre className="mermaid-error">Mermaid Error: {error}</pre>;
    return <div ref={ref} className="mermaid-block" />;
}
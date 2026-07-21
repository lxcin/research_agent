import { useState, useEffect } from 'react';

interface ToolInfo {
    name: string;
    description: string;
    category: string;
}

export default function ToolsPanel() {
    const [tools, setTools] = useState<ToolInfo[]>([]);

    useEffect(() => {
        fetch('/api/tools').then(r => r.json()).then(setTools).catch(() => {});
    }, []);

    const categories = [...new Set(tools.map(t => t.category))];

    return (
        <div className="tools-panel">
            <div className="tp-header">
                <h3>工具注册表 ({tools.length})</h3>
            </div>
            <div className="tp-list">
                {categories.map(cat => (
                    <div key={cat} className="tp-category">
                        <div className="tp-cat-name">{cat}</div>
                        {tools.filter(t => t.category === cat).map(t => (
                            <div key={t.name} className="tp-item">
                                <div className="tp-item-name">{t.name}</div>
                                <div className="tp-item-desc">{t.description}</div>
                            </div>
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
}
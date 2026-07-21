import { useCallback } from 'react';

export interface PlanItem {
    id: string;
    text: string;
    done: boolean;
}

interface PlanPanelProps {
    items: PlanItem[];
    onToggle: (id: string) => void;
    onClear: () => void;
}

export default function PlanPanel({ items, onToggle, onClear }: PlanPanelProps) {
    const done = items.filter(i => i.done).length;

    return (
        <div className="plan-panel">
            <div className="pp-header">
                <h3>执行计划 {items.length > 0 && `(${done}/${items.length})`}</h3>
                {items.length > 0 && (
                    <button className="pp-clear-btn" onClick={onClear} title="清除已完成">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
                    </button>
                )}
            </div>
            <div className="pp-list">
                {items.length === 0 ? (
                    <div className="pp-empty">输入复杂任务自动生成计划</div>
                ) : (
                    items.map(item => (
                        <div key={item.id} className={`pp-item${item.done ? ' pp-done' : ''}`}
                             onClick={() => onToggle(item.id)}>
                            <div className={`pp-checkbox${item.done ? ' pp-checked' : ''}`}>
                                {item.done && <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M20 6L9 17l-5-5"/></svg>}
                            </div>
                            <span className="pp-text">{item.text}</span>
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}
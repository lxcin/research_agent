import { useCallback } from 'react';

interface ChatTabsProps {
    chats: { id: string; title: string }[];
    activeId: string;
    onSelect: (id: string) => void;
    onNew: () => void;
    onClose: (id: string) => void;
}

export default function ChatTabs({ chats, activeId, onSelect, onNew, onClose }: ChatTabsProps) {
    if (chats.length <= 1) return null;

    return (
        <div className="chat-tabs">
            {chats.map(c => (
                <div key={c.id} className={`chat-tab${c.id === activeId ? ' active' : ''}`}
                     onClick={() => onSelect(c.id)}>
                    <span className="chat-tab-title">{c.title || '新对话'}</span>
                    {chats.length > 1 && (
                        <button className="chat-tab-close" onClick={e => { e.stopPropagation(); onClose(c.id); }}
                                title="关闭">×</button>
                    )}
                </div>
            ))}
            <button className="chat-tab-new" onClick={onNew} title="新建对话">+</button>
        </div>
    );
}
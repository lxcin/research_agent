import type { ChatInfo } from '../types';

interface ProjectSidebarProps {
  isOpen: boolean;
  onToggle: () => void;
  chats: ChatInfo[];
  currentChatId: string;
  onSelectChat: (id: string) => void;
  onNewChat: () => void;
  onDeleteChat: (id: string) => void;
  onSetChatWorkspace: () => void;
}

function displayPath(dir: string): string {
  if (!dir) return '默认';
  const parts = dir.replace(/\\/g, '/').split('/').filter(Boolean);
  if (parts.length <= 2) return dir;
  return '.../' + parts.slice(-2).join('/');
}

export default function ProjectSidebar({
  isOpen, onToggle, chats, currentChatId,
  onSelectChat, onNewChat, onDeleteChat, onSetChatWorkspace,
}: ProjectSidebarProps) {
  const activeChat = chats.find(c => c.chat_id === currentChatId);
  const workspaceDir = activeChat?.workspace_dir || '';

  return (
    <>
      <div className={`sidebar-overlay${isOpen ? ' visible' : ''}`} onClick={onToggle} />
      <aside className={`sidebar${isOpen ? ' open' : ''}`}>
        <div className="sidebar-header">
          <button className="sidebar-toggle" onClick={onToggle}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M15 18l-6-6 6-6"/></svg>
          </button>
          <span className="sidebar-title">对话</span>
        </div>

        <div className="sidebar-section-header">
          <span className="sidebar-section-title">对话 ({chats.length})</span>
          <button className="sidebar-new-btn" onClick={onNewChat} title="新建对话">+</button>
        </div>

        <div className="sidebar-list">
          {chats.length === 0 ? (
            <div className="sidebar-empty">暂无对话，点击 + 新建</div>
          ) : (
            chats.map(c => (
              <div key={c.chat_id}
                   className={`sidebar-item${c.chat_id === currentChatId ? ' active' : ''}`}
                   onClick={() => onSelectChat(c.chat_id)}>
                <div className="sidebar-item-main">
                  <span className="sidebar-item-name">{c.title || '新对话'}</span>
                  <span className="sidebar-item-time">{c.workspace_dir ? displayPath(c.workspace_dir) : '默认'}</span>
                </div>
                <button className="sidebar-delete-btn" title="删除对话"
                        onClick={e => { e.stopPropagation(); onDeleteChat(c.chat_id); }}>
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/></svg>
                </button>
              </div>
            ))
          )}
        </div>

        <div className="sidebar-workspace">
          <div className="sw-header">
            <span>当前工作区</span>
          </div>
          <div className="sw-path">{displayPath(workspaceDir)}</div>
          <div className="sw-actions">
            <button className="sw-btn" onClick={onSetChatWorkspace}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" width="12" height="12"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
              更改目录
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

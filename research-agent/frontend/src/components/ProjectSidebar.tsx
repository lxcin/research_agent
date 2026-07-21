import { useState, useEffect } from 'react';
import type { Project } from '../types';

interface ProjectSidebarProps {
    isOpen: boolean;
    onToggle: () => void;
    projects: Project[];
    currentProjectId: string;
    onSelectProject: (id: string) => void;
    onNewProject: () => void;
    onRenameProject: (id: string, name: string) => void;
    onDeleteProject: (id: string) => void;
    onSetWorkspace: (id: string, dir: string) => void;
}

export default function ProjectSidebar({
    isOpen, onToggle, projects, currentProjectId,
    onSelectProject, onNewProject, onRenameProject, onDeleteProject, onSetWorkspace,
}: ProjectSidebarProps) {
    const [search, setSearch] = useState('');
    const [wsDir, setWsDir] = useState('');
    const [projPapers, setProjPapers] = useState<any[]>([]);

    useEffect(() => {
        if (!currentProject) return;
        fetch(`/api/projects/${currentProject.id}`)
            .then(r => r.json())
            .then(p => setWsDir(p.workspace_dir || ''))
            .catch(() => setWsDir(''));
        fetch(`/api/projects/${currentProject.id}/papers`)
            .then(r => r.json())
            .then(setProjPapers)
            .catch(() => setProjPapers([]));
    }, [currentProjectId]);

    const filtered = projects.filter(p =>
        !search || p.name.toLowerCase().includes(search.toLowerCase())
    );

    const currentProject = projects.find(p => p.id === currentProjectId);

    function pickFolder() {
        // pywebview native picker (desktop mode)
        const w = window as any;
        if (w.pywebview?.api?.pick_folder) {
            w.pywebview.api.pick_folder().then((path: string) => {
                if (path) { onSetWorkspace(currentProjectId, path); setWsDir(path); }
            }).catch(() => {});
            return;
        }
        // File System Access API (Chrome/Edge)
        if (w.showDirectoryPicker) {
            w.showDirectoryPicker().then((handle: any) => {
                const name = handle.name || '';
                onSetWorkspace(currentProjectId, name); setWsDir(name);
            }).catch(() => {});
            return;
        }
        // Fallback: prompt for path
        const path = prompt('输入工作区目录路径（如 D:/research/project）：');
        if (path) { onSetWorkspace(currentProjectId, path.trim()); setWsDir(path.trim()); }
    }

    function currentWorkspace(): string {
        if (!currentProject) return '默认（系统沙箱）';
        return wsDir || '默认（系统沙箱）';
    }

    return (
        <>
            <div className={`sidebar-overlay${isOpen ? ' visible' : ''}`} onClick={onToggle} />
            <aside className={`sidebar${isOpen ? ' open' : ''}`}>
                <div className="sidebar-header">
                    <button className="sidebar-toggle" onClick={onToggle}>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M15 18l-6-6 6-6"/></svg>
                    </button>
                    <span className="sidebar-title">项目</span>
                    <button className="sidebar-new-btn" onClick={onNewProject} title="新建项目">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14"/><path d="M5 12h14"/></svg>
                    </button>
                </div>

                <div className="sidebar-search">
                    <input type="text" placeholder="搜索项目..." value={search}
                           onChange={e => setSearch(e.target.value)} />
                </div>

                <div className="sidebar-list">
                    {filtered.length === 0 ? (
                        <div className="sidebar-empty">暂无项目</div>
                    ) : (
                        filtered.map(p => (
                            <div key={p.id}
                                 className={`sidebar-item${p.id === currentProjectId ? ' active' : ''}`}
                                 onClick={() => onSelectProject(p.id)}>
                                <div className="sidebar-item-main">
                                    <span className="sidebar-item-name">{p.name}</span>
                                    <span className="sidebar-item-time">{p.updated}</span>
                                </div>
                                <div className="sidebar-item-actions">
                                    <button className="sidebar-delete-btn" title="删除项目"
                                            onClick={e => { e.stopPropagation(); if (confirm('确定删除此项目？')) onDeleteProject(p.id); }}>
                                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6"/><path d="M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2"/></svg>
                                    </button>
                                </div>
                            </div>
                        ))
                    )}
                </div>

                {currentProject && projPapers.length > 0 && (
                    <div className="sidebar-section">
                        <span className="sidebar-section-title">项目论文 ({projPapers.length})</span>
                        {projPapers.map((p: any) => (
                            <div key={p.id} className="sidebar-paper-item" title={p.title}>
                                <span className="sp-name">{p.title}</span>
                                <span className="sp-year">{p.year || ''}</span>
                            </div>
                        ))}
                    </div>
                )}

                {currentProject && (
                    <div className="sidebar-workspace">
                        <div className="sw-header">
                            <span>工作区</span>
                        </div>
                        <div className="sw-path">{currentWorkspace()}</div>
                        <div className="sw-actions">
                            <button className="sw-btn" onClick={() => pickFolder()}>
                                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" width="12" height="12"><path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z"/></svg>
                                选择目录
                            </button>
                            <button className="sw-btn sw-btn-reset" onClick={() => onSetWorkspace(currentProjectId, '')}>
                                恢复默认
                            </button>
                        </div>
                    </div>
                )}
            </aside>
        </>
    );
}
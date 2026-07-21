import { useState, useCallback, useEffect } from 'react';
import TopBar from './components/TopBar';
import ChatArea from './components/ChatArea';
import ChatInput from './components/ChatInput';
import GraphWindow from './components/GraphWindow';
import FloatingWindow from './components/FloatingWindow';
import ForceGraph from './components/ForceGraph';
import ProjectSidebar from './components/ProjectSidebar';
import SettingsPanel from './components/SettingsPanel';
import PaperLibrary from './components/PaperLibrary';
import ToolsPanel from './components/ToolsPanel';
import WorkspaceSidebar from './components/WorkspaceSidebar';
import ChatTabs from './components/ChatTabs';
import ErrorBoundary from './components/ErrorBoundary';
import type { PlanItem } from './components/PlanPanel';
import type { Message, GraphData, PaperTree, Project, ApiConfig, ToolCall } from './types';

const EMPTY_GRAPH: GraphData = { nodes: [], edges: [] };
const EMPTY_TREES: Record<string, PaperTree> = {};
const DEFAULT_PROJECT_ID = '__default__';

let msgId = 0;
function generateId() { return 'msg_' + (++msgId) + '_' + Date.now(); }

function formatEventText(type: string, data: Record<string, any>): string {
  switch (type) {
    case 'step': return `\n> ${data.text || ''}\n`;
    case 'plan': return `\n📋 **执行计划**\n${(data.items || []).map((i: string) => `- ${i}`).join('\n')}\n\n`;
    case 'sources': return `${data.text || ''}\n\n`;
    case 'recall': {
      const p5 = data.p5 || '?';
      const p8 = data.p8 || '';
      const p10 = data.p10 || '';
      const rec = data.recall || '?';
      const rp = data.recall_pool ? ` R@${data.recall_pool}=${rec}` : '';
      const pts = p8 ? ` P@8=${p8}` : '';
      const pt10 = p10 ? ` P@10=${p10}` : '';
      return `\n| P@5=${p5}${pts}${pt10}${rp}\n`;
    }
    case 'error': return data.text || '';
    default: return '';
  }
}

function loadApiConfig(): ApiConfig {
  try {
    const saved = localStorage.getItem('pp-api-config');
    if (saved) return JSON.parse(saved);
  } catch {}
  return { provider: '', apiKey: '', baseUrl: '', model: '' };
}

function loadProjects(): Project[] {
  try {
    const saved = localStorage.getItem('pp-projects');
    if (saved) return JSON.parse(saved);
  } catch {}
  return [];
}

function saveProjects(projects: Project[]) {
  localStorage.setItem('pp-projects', JSON.stringify(projects));
}

type ChatsMap = Record<string, Record<string, Message[]>>;
type ChatMeta = { id: string; title: string };
type ChatMetasMap = Record<string, ChatMeta[]>;

function loadAllMessages(): ChatsMap {
  try { const s = localStorage.getItem('pp-chats'); if (s) return JSON.parse(s); } catch {}
  return {};
}
function saveAllMessages(map: ChatsMap) { localStorage.setItem('pp-chats', JSON.stringify(map)); }

function loadChatMetas(): ChatMetasMap {
  try { const s = localStorage.getItem('pp-chat-metas'); if (s) return JSON.parse(s); } catch {}
  return {};
}
function saveChatMetas(map: ChatMetasMap) { localStorage.setItem('pp-chat-metas', JSON.stringify(map)); }

function generateChatId() { return 'chat_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8); }

export default function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => (localStorage.getItem('pp-theme') as 'light' | 'dark') || 'dark');
  const [currentProjectId, setCurrentProjectId] = useState<string>(DEFAULT_PROJECT_ID);
  const [allMessages, setAllMessages] = useState<ChatsMap>(loadAllMessages);
  const [activeChatIds, setActiveChatIds] = useState<Record<string, string>>({});
  const [chatMetas, setChatMetas] = useState<ChatMetasMap>(loadChatMetas);
  const [loading, setLoading] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [papersOpen, setPapersOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [wsRefresh, setWsRefresh] = useState(0);
  const [citeDetail, setCiteDetail] = useState<any>(null);
  const [planItems, setPlanItems] = useState<PlanItem[]>([]);
  const [projects, setProjects] = useState<Project[]>(loadProjects);
  const [apiConfig, setApiConfig] = useState<ApiConfig>(loadApiConfig);
  const [graphData, setGraphData] = useState<GraphData>(EMPTY_GRAPH);
  const [paperTrees, setPaperTrees] = useState<Record<string, PaperTree>>(EMPTY_TREES);

  const currentChatId = activeChatIds[currentProjectId] || Object.keys(allMessages[currentProjectId] || {})[0] || '__init__';

  useEffect(() => {
    if (!activeChatIds[currentProjectId]) {
      const cid = generateChatId();
      setAllMessages(prev => ({ ...prev, [currentProjectId]: { [cid]: [] } }));
      setActiveChatIds(prev => ({ ...prev, [currentProjectId]: cid }));
      setChatMetas(prev => ({ ...prev, [currentProjectId]: [{ id: cid, title: '' }] }));
    }
  }, [currentProjectId]);

  const messages = (allMessages[currentProjectId]?.[currentChatId]) || [];
  const currentProject = projects.find(p => p.id === currentProjectId);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('pp-theme', theme);
  }, [theme]);

  useEffect(() => {
    saveAllMessages(allMessages);
  }, [allMessages]);

  useEffect(() => {
    saveChatMetas(chatMetas);
  }, [chatMetas]);

  useEffect(() => {
    saveProjects(projects);
  }, [projects]);

  useEffect(() => {
    fetch('/api/graph')
      .then(r => r.json())
      .then(data => setGraphData(data))
      .catch(() => {});
  }, []);

  const handleSaveApiConfig = useCallback((config: ApiConfig) => {
    setApiConfig(config);
    localStorage.setItem('pp-api-config', JSON.stringify(config));
  }, []);

  const setMessages = useCallback((updater: (prev: Message[]) => Message[]) => {
    setAllMessages(prev => {
      const projectMsgs = prev[currentProjectId] || {};
      const chatMsgs = projectMsgs[currentChatId] || [];
      return {
        ...prev,
        [currentProjectId]: { ...projectMsgs, [currentChatId]: updater(chatMsgs) },
      };
    });
  }, [currentProjectId, currentChatId]);

  const handleSend = useCallback((text: string) => {
    // Auto-title chat on first message
    const existing = allMessages[currentProjectId]?.[currentChatId];
    if (!existing || existing.length === 0) {
      const title = text.slice(0, 30);
      setChatMetas(prev => ({
        ...prev,
        [currentProjectId]: (prev[currentProjectId] || []).map(c =>
          c.id === currentChatId ? { ...c, title } : c),
      }));
    }

    setMessages(prev => [...prev, { id: generateId(), role: 'user', text, timestamp: Date.now(), projectId: currentProjectId }]);

    if (!apiConfig.apiKey) {
      setMessages(prev => [...prev, { id: generateId(), role: 'ai', text: '请先配置 API。点击右上角 设置按钮，填入你的 API Key 和模型信息。', timestamp: Date.now(), projectId: currentProjectId }]);
      return;
    }

    setLoading(true);
    const msgId = generateId();

    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, config: apiConfig, project_id: currentProjectId }),
    }).then(async response => {
      if (!response.ok) throw new Error('API error');
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');
      const decoder = new TextDecoder();
      let aiText = '';
      let buffer = '';

      setMessages(prev => [...prev, { id: msgId, role: 'ai', text: '', timestamp: Date.now(), projectId: currentProjectId }]);

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const data = JSON.parse(line.slice(6));
            if (data.type === 'chunk') {
              aiText += data.text;
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: aiText } : m));
            } else if (data.type === 'step' && data.step === 'generate') {
              aiText += `\n> ${data.text}\n`;
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: aiText } : m));
              markPlanDone('生成'); markPlanDone('回答'); markPlanDone('分析');
            } else if (data.type === 'action') {
              const a = data.action;
              const q = (data.query || '').slice(0, 80);
              const icon = a === 'retrieve' ? '🔍' : a === 'search_papers' ? '📡' : a === 'read_paper' ? '📖' : a === 'update_notes' ? '📝' : '🔧';
              aiText += `\n> ${icon} **${a}**: ${q}\n`;
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: aiText } : m));
            } else if (data.type === 'tool' && data.status === 'file_saved') {
              setWsRefresh(prev => prev + 1);
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: aiText } : m));
            } else if (data.type === 'tool') {
              const st = data.status;
              if (st === 'start' && data.tool === 'shell_exec') {
                aiText += '<div class="tool-inline-exec">running... ' + (data.command || '').slice(0, 40) + '</div>\n';
              } else if (st === 'done') {
                aiText += `> ✅ 完成\n`;
                if (data.path && (data.tool === 'file_write' || data.tool === 'file_edit')) {
                  aiText += `\n<div class="file-link" data-path="${data.path}">📄 ${data.path}</div>\n`;
                }
                if (data.tool === 'retrieve') markPlanDone('检索');
                else if (data.tool === 'search_papers') markPlanDone('搜索');
                else if (data.tool === 'read_paper') markPlanDone('阅读');
              } else if (st === 'empty' || st === 'failed') {
                aiText += `> ❌ ${st}\n`;
              } else if (st === 'found') {
                aiText += `> 📄 找到 ${data.count || 0} 篇\n`;
              } else if (st === 'escalate') {
                aiText += `> ⬆ 升级 arXiv 搜索\n`;
              } else if (st === 'error') {
                aiText += `> ❌ ${data.error || 'error'}\n`;
              }
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: aiText } : m));
            } else {
              aiText += formatEventText(data.type, data);
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: aiText } : m));
            }
            if (data.type === 'citations' && data.papers) {
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, citations: data.papers } : m));
            }
            if (data.type === 'plan' && data.items) {
              const items: PlanItem[] = data.items.map((text: string, i: number) => ({
                id: `plan_${Date.now()}_${i}`, text, done: false,
              }));
              setPlanItems(prev => [...prev, ...items]);
            }
          } catch {}
        }
      }

      fetch('/api/graph')
        .then(r => r.json())
        .then(data => setGraphData(data))
        .catch(() => {});
    }).catch(() => {
      setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: '请求失败，请检查 API 配置和网络连接。' } : m));
    }).finally(() => setLoading(false));
  }, [apiConfig, currentProjectId, setMessages]);

const handleSelectProject = useCallback((id: string) => {
    setCurrentProjectId(id);
    setProjectOpen(false);
  }, []);

  const handleNewChat = useCallback(() => {
    const cid = generateChatId();
    setAllMessages(prev => ({
      ...prev,
      [currentProjectId]: { ...(prev[currentProjectId] || {}), [cid]: [] },
    }));
    setActiveChatIds(prev => ({ ...prev, [currentProjectId]: cid }));
    const meta: ChatMeta = { id: cid, title: '' };
    setChatMetas(prev => ({
      ...prev,
      [currentProjectId]: [...(prev[currentProjectId] || []), meta],
    }));
  }, [currentProjectId]);

  const handleCloseChat = useCallback((id: string) => {
    const chats = allMessages[currentProjectId] || {};
    if (Object.keys(chats).length <= 1) return;
    setAllMessages(prev => {
      const p = { ...(prev[currentProjectId] || {}) };
      delete p[id];
      return { ...prev, [currentProjectId]: p };
    });
    setChatMetas(prev => ({
      ...prev,
      [currentProjectId]: (prev[currentProjectId] || []).filter(c => c.id !== id),
    }));
    if (id === currentChatId) {
      const remaining = Object.keys(chats).filter(k => k !== id);
      if (remaining.length > 0) setActiveChatIds(prev => ({ ...prev, [currentProjectId]: remaining[0] }));
    }
  }, [currentProjectId, currentChatId, allMessages]);

  const getCurrentChats = useCallback((): ChatMeta[] => {
    return chatMetas[currentProjectId] || [];
  }, [chatMetas, currentProjectId]);

  const handleNewProject = useCallback(() => {
    fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: '未命名项目' }),
    })
      .then(r => r.json())
      .then(newProj => {
        setProjects(prev => [{ ...newProj, steps: [], progress: 0 }, ...prev]);
        setCurrentProjectId(newProj.id);
        setProjectOpen(false);
      })
      .catch(() => {
        // Fallback: use timestamp-based ID
        const fallback: Project = {
          id: 'p_' + Date.now(),
          name: '未命名项目',
          status: 'active', updated: '刚刚',
          created: new Date().toISOString().slice(0, 10),
          summary: '', progress: 0, steps: [],
        };
        setProjects(prev => [fallback, ...prev]);
        setCurrentProjectId(fallback.id);
      });
  }, []);

  const handleRenameProject = useCallback((id: string, name: string) => {
    setProjects(prev => prev.map(p => p.id === id ? { ...p, name } : p));
  }, []);

  const handleDeleteProject = useCallback((id: string) => {
    setProjects(prev => prev.filter(p => p.id !== id));
    if (currentProjectId === id) setCurrentProjectId(DEFAULT_PROJECT_ID);
  }, [currentProjectId]);

  const handleSetWorkspace = useCallback((projectId: string, dir: string) => {
    fetch(`/api/projects/${projectId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ workspace_dir: dir || null }),
    }).catch(() => {});
  }, []);

  const handleLoadPaperTree = useCallback((paperId: string) => {
    fetch(`/api/graph/${paperId}`)
      .then(r => r.json())
      .then(tree => setPaperTrees(prev => ({ ...prev, [paperId]: tree })))
      .catch(() => {});
  }, []);

  const handleTogglePlanItem = useCallback((id: string) => {
    setPlanItems(prev => prev.map(p => p.id === id ? { ...p, done: !p.done } : p));
  }, []);

  const markPlanDone = useCallback((keyword: string) => {
    setPlanItems(prev => {
      const idx = prev.findIndex(p => !p.done && p.text.includes(keyword));
      if (idx === -1) return prev;
      return prev.map((p, i) => i === idx ? { ...p, done: true } : p);
    });
  }, []);

  const handleClearPlan = useCallback(() => {
    setPlanItems(prev => prev.filter(p => !p.done));
  }, []);

  return (
    <ErrorBoundary>
    <div className="app" onClick={(e) => {
        const target = e.target as HTMLElement;
        if (target.classList.contains('citation-link')) {
          const pid = target.getAttribute('data-pid');
          const title = target.getAttribute('title') || '';
          const pidVal = pid || '';
          for (const chatMsgs of Object.values(allMessages[currentProjectId] || {})) {
            for (const msg of chatMsgs) {
              if (msg.citations) {
                const found = msg.citations.find((c: any) => c.id === pidVal);
                if (found) { setCiteDetail(found); return; }
              }
            }
          }
          setCiteDetail({ id: pid, title });
        }
        if (target.classList.contains('file-link')) {
          setWorkspaceOpen(true);
        }
      }}>
      <TopBar
        projectName={currentProject?.name || '未命名项目'}
        onProjectNameChange={name => {
          if (currentProject) handleRenameProject(currentProject.id, name);
        }}
        graphOpen={graphOpen}
        projectOpen={sidebarOpen}
        onGraphToggle={() => setGraphOpen(prev => !prev)}
        onProjectToggle={() => setSidebarOpen(prev => !prev)}
        onPapersToggle={() => setPapersOpen(prev => !prev)}
        onToolsToggle={() => setToolsOpen(prev => !prev)}
        onWorkspaceToggle={() => setWorkspaceOpen(prev => !prev)}
        theme={theme}
        onThemeToggle={() => setTheme(prev => prev === 'light' ? 'dark' : 'light')}
        onSettingsOpen={() => setSettingsOpen(true)}
        hasApiConfig={!!apiConfig.apiKey}
        hasMessages={messages.length > 0}
        onClearMessages={() => {
          if (confirm('确定清空当前项目对话？')) {
            setAllMessages(prev => { const next = { ...prev }; delete next[currentProjectId]; return next; });
          }
        }}
      />
      <ChatTabs
        chats={getCurrentChats()}
        activeId={currentChatId}
        onSelect={id => setActiveChatIds(prev => ({ ...prev, [currentProjectId]: id }))}
        onNew={handleNewChat}
        onClose={handleCloseChat}
      />
      <div className="main-content">
        <div className="chat-col">
          <ChatArea
            messages={messages}
            welcome
            onSuggestionClick={handleSend}
            planItems={planItems}
            onTogglePlanItem={handleTogglePlanItem}
            onClearPlan={handleClearPlan}
          />
          <ChatInput onSend={handleSend} disabled={loading} />
        </div>
        <WorkspaceSidebar
          projectId={currentProjectId}
          isOpen={workspaceOpen}
          onToggle={() => setWorkspaceOpen(prev => !prev)}
          refreshKey={wsRefresh}
        />
      </div>

      <GraphWindow isOpen={graphOpen} onToggle={() => setGraphOpen(false)}>
        <ForceGraph
          data={graphData}
          paperTrees={paperTrees}
          onLoadPaperTree={handleLoadPaperTree}
        />
      </GraphWindow>

      <ProjectSidebar
        isOpen={sidebarOpen}
        onToggle={() => setSidebarOpen(prev => !prev)}
        projects={projects}
        currentProjectId={currentProjectId}
        onSelectProject={handleSelectProject}
        onNewProject={handleNewProject}
        onRenameProject={handleRenameProject}
        onDeleteProject={handleDeleteProject}
        onSetWorkspace={handleSetWorkspace}
      />

      <FloatingWindow
        isOpen={papersOpen}
        onToggle={() => setPapersOpen(false)}
        title="论文库"
        defaultWidth={420} defaultHeight={500}
        defaultTop={80} defaultLeft={360}
      >
        <PaperLibrary />
      </FloatingWindow>

      <FloatingWindow
        isOpen={toolsOpen}
        onToggle={() => setToolsOpen(false)}
        title="工具面板"
        defaultWidth={360} defaultHeight={400}
        defaultTop={80} defaultLeft={420}
      >
        <ToolsPanel />
      </FloatingWindow>

      {settingsOpen && (
        <SettingsPanel
          onClose={() => setSettingsOpen(false)}
          config={apiConfig}
          onSave={handleSaveApiConfig}
        />
      )}
    </div>

      {citeDetail && (
        <div className="cite-popup-overlay" onClick={() => setCiteDetail(null)}>
          <div className="cite-popup" onClick={e => e.stopPropagation()}>
            <div className="cite-popup-header">
              <h3>{citeDetail.title}</h3>
              <button onClick={() => setCiteDetail(null)}>x</button>
            </div>
            {citeDetail.authors && citeDetail.authors.length > 0 && (
              <div className="cite-popup-authors">{(citeDetail.authors || []).join(', ')}{citeDetail.year ? ' (' + citeDetail.year + ')' : ''}</div>
            )}
            {citeDetail.doi && <div className="cite-popup-doi">{citeDetail.doi}</div>}
            {citeDetail.abstract && <p className="cite-popup-abstract">{citeDetail.abstract}</p>}
          </div>
        </div>
      )}

    </ErrorBoundary>
  );
}
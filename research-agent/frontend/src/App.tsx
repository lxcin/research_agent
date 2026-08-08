import { useState, useCallback, useEffect, useRef } from 'react';
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
import SkillsPanel from './components/SkillsPanel';
import ChatTabs from './components/ChatTabs';
import ErrorBoundary from './components/ErrorBoundary';
import type { Message, MessageSection, PlanItem, ReplySection, GraphData, PaperTree, ApiConfig, ChatInfo } from './types';

const EMPTY_GRAPH: GraphData = { nodes: [], edges: [] };
const EMPTY_TREES: Record<string, PaperTree> = {};

let msgId = 0;
function generateId() { return 'msg_' + (++msgId) + '_' + Date.now(); }
function generateChatId() { return 'chat_' + Date.now() + '_' + Math.random().toString(36).slice(2, 8); }

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

const DEFAULT_WORKSPACE = 'default';

export default function App() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() => (localStorage.getItem('pp-theme') as 'light' | 'dark') || 'dark');
  const [allMessages, setAllMessages] = useState<Record<string, Message[]>>({});
  const [currentChatId, setCurrentChatId] = useState<string>('');
  const [chatMetas, setChatMetas] = useState<ChatInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [graphOpen, setGraphOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [papersOpen, setPapersOpen] = useState(false);
  const [toolsOpen, setToolsOpen] = useState(false);
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const [skillsOpen, setSkillsOpen] = useState(false);
  const [wsRefresh, setWsRefresh] = useState(0);
  const [citeDetail, setCiteDetail] = useState<any>(null);
  const [planItems, setPlanItems] = useState<PlanItem[]>([]);
  const [apiConfig, setApiConfig] = useState<ApiConfig>(loadApiConfig);
  const [graphData, setGraphData] = useState<GraphData>(EMPTY_GRAPH);
  const [paperTrees, setPaperTrees] = useState<Record<string, PaperTree>>(EMPTY_TREES);

  const chatMetasRef = useRef(chatMetas);
  useEffect(() => { chatMetasRef.current = chatMetas; }, [chatMetas]);
  const currentChatIdRef = useRef(currentChatId);
  useEffect(() => { currentChatIdRef.current = currentChatId; }, [currentChatId]);

  const getActiveChatWorkspace = (): string => {
    const meta = chatMetasRef.current.find(c => c.chat_id === currentChatIdRef.current);
    return meta?.workspace_dir || DEFAULT_WORKSPACE;
  };

  const activeChat = chatMetas.find(c => c.chat_id === currentChatId);
  const activeChatTitle = activeChat?.title || '';
  const activeChatWorkspace = activeChat?.workspace_dir || DEFAULT_WORKSPACE;

  useEffect(() => {
    fetch('/api/chats')
      .then(r => r.json())
      .then((chats: ChatInfo[]) => {
        const list = Array.isArray(chats) ? chats : [];
        setChatMetas(list);
        if (list.length > 0) {
          setCurrentChatId(list[0].chat_id);
          fetch(`/api/chats/${encodeURIComponent(list[0].chat_id)}`)
            .then(r => r.json())
            .then((chat: any) => {
              if (chat && chat.turns) {
                const msgs = chat.turns.flatMap((t: any) => [
                  { id: `hist_u_${t.round}`, role: 'user' as const, text: t.user || '', timestamp: Date.now() },
                  { id: `hist_a_${t.round}`, role: 'ai' as const, text: t.assistant || '', sections: t.sections || [], timestamp: Date.now() },
                ]);
                setAllMessages({ [list[0].chat_id]: msgs });
              } else {
                setAllMessages({ [list[0].chat_id]: [] });
              }
            }).catch(() => setAllMessages({ [list[0].chat_id]: [] }));
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (!currentChatId) return;
    fetch(`/api/chats/${encodeURIComponent(currentChatId)}`)
      .then(r => r.json())
      .then((chat: any) => {
        if (chat && chat.turns) {
          const msgs = chat.turns.flatMap((t: any) => [
            { id: `hist_u_${t.round}`, role: 'user' as const, text: t.user || '', timestamp: Date.now() },
            { id: `hist_a_${t.round}`, role: 'ai' as const, text: t.assistant || '', sections: t.sections || [], timestamp: Date.now() },
          ]);
          setAllMessages(prev => ({ ...prev, [currentChatId]: msgs }));
        }
      }).catch(() => {});
  }, [currentChatId]);

  const messages = allMessages[currentChatId] || [];

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('pp-theme', theme);
  }, [theme]);

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
    setAllMessages(prev => ({
      ...prev,
      [currentChatId]: updater(prev[currentChatId] || []),
    }));
  }, [currentChatId]);

  const handleChatTitleChange = useCallback((title: string) => {
    setChatMetas(prev => prev.map(c => c.chat_id === currentChatId ? { ...c, title } : c));
  }, [currentChatId]);

  const handleSend = useCallback((text: string) => {
    let cid = currentChatIdRef.current;
    if (!cid) {
      cid = generateChatId();
      setCurrentChatId(cid);
      setChatMetas(prev => [...prev, {
        chat_id: cid,
        title: text.slice(0, 30),
        created_at: new Date().toISOString(),
        turn_count: 0,
        workspace_dir: DEFAULT_WORKSPACE,
      }]);
    } else {
      setChatMetas(prev =>
        prev.map(c => c.chat_id === cid && (!c.title || c.title === '新对话') ? { ...c, title: text.slice(0, 30) } : c)
      );
    }

    const appendMsg = (msg: Message) => {
      setAllMessages(prev => ({ ...prev, [cid]: [...(prev[cid] || []), msg] }));
    };
    appendMsg({ id: generateId(), role: 'user', text, timestamp: Date.now() });

    setLoading(true);
    const msgId = generateId();
    const ws = getActiveChatWorkspace();

    fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, config: apiConfig, workspace_dir: ws, chat_id: cid }),
    }).then(async response => {
      if (!response.ok) throw new Error('API error');
      const reader = response.body?.getReader();
      if (!reader) throw new Error('No reader');
      const decoder = new TextDecoder();
      let currentSections: MessageSection[] = [];
      let replySection: ReplySection | null = null;
      let buffer = '';

      setMessages(prev => [...prev, { id: msgId, role: 'ai', text: '', timestamp: Date.now() }]);

      const updateMsg = (sections: MessageSection[], replyText: string) => {
        setMessages(prev => prev.map(m => m.id === msgId ? { ...m, sections: [...sections], text: replyText } : m));
      };

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

            if (data.type === 'thinking') {
              currentSections.push({ type: 'thinking', text: data.text });
              updateMsg(currentSections, replySection?.text || '');
            }
            else if (data.type === 'plan') {
              const rawItems = data.items || [];
              if (rawItems.length > 0 && typeof rawItems[0] === 'object') {
                const items: PlanItem[] = rawItems.map((item: any) => ({
                  id: item.id || `p_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
                  text: item.text || '',
                  done: item.done || false,
                }));
                currentSections.push({ type: 'plan', items });
                setPlanItems(prev => [...prev, ...items]);
                updateMsg(currentSections, replySection?.text || '');
              } else {
                const items: PlanItem[] = rawItems.map((t: string, i: number) => ({
                  id: `plan_${Date.now()}_${i}`, text: t, done: false,
                }));
                currentSections.push({ type: 'plan', items });
                setPlanItems(prev => [...prev, ...items]);
                updateMsg(currentSections, replySection?.text || '');
              }
            }
            else if (data.type === 'plan_done') {
              for (let i = currentSections.length - 1; i >= 0; i--) {
                const s = currentSections[i];
                if (s.type === 'plan') {
                  s.items = s.items.map(item => item.id === data.id ? { ...item, done: true } : item);
                  break;
                }
              }
              setPlanItems(prev => prev.map(p => p.id === data.id ? { ...p, done: true } : p));
              updateMsg(currentSections, replySection?.text || '');
            }
            else if (data.type === 'tool_start') {
              currentSections.push({
                type: 'tool',
                id: data.id,
                name: data.name,
                input: data.input || {},
                status: 'running',
              });
              updateMsg(currentSections, replySection?.text || '');
            }
            else if (data.type === 'tool_end') {
              const tool = currentSections.find(s => s.type === 'tool' && s.id === data.id);
              if (tool && tool.type === 'tool') {
                tool.status = data.status === 'error' ? 'error' : 'success';
                tool.output = data.output;
              }
              updateMsg(currentSections, replySection?.text || '');
            }
            else if (data.type === 'file_change') {
              const tool = currentSections.find(s => s.type === 'tool' && s.id === data.tool_id);
              if (tool && tool.type === 'tool') {
                tool.fileChange = { path: data.path, action: data.action, diff: data.diff };
              }
              setWsRefresh(prev => prev + 1);
              updateMsg(currentSections, replySection?.text || '');
            }
            else if (data.type === 'reply') {
              if (!replySection) {
                replySection = { type: 'reply', text: data.text };
                currentSections.push(replySection);
              } else {
                replySection.text += data.text;
              }
              updateMsg(currentSections, replySection.text);
            }
            else if (data.type === 'done') {
              updateMsg(currentSections, replySection?.text || '');
              fetch('/api/graph')
                .then(r => r.json())
                .then(data => setGraphData(data))
                .catch(() => {});
            }
            else if (data.type === 'citations' && data.papers) {
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, citations: data.papers } : m));
            }
            else if (data.type === 'error') {
              currentSections.push({ type: 'thinking', text: `❌ ${data.text || 'Error'}` });
              updateMsg(currentSections, replySection?.text || '');
            }
            else if (data.type === 'chunk') {
              if (!replySection) {
                replySection = { type: 'reply', text: data.text };
                currentSections.push(replySection);
              } else {
                replySection.text += data.text;
              }
              updateMsg(currentSections, replySection.text);
            }
            else if (data.type === 'step' || data.type === 'action' || data.type === 'tool' || data.type === 'sources' || data.type === 'recall') {
              const text = formatEventText(data.type, data);
              if (text) {
                if (!replySection) {
                  replySection = { type: 'reply', text };
                  currentSections.push(replySection);
                } else {
                  replySection.text += text;
                }
              }
              if (data.type === 'step' && data.step === 'generate') {
                markPlanDone('生成'); markPlanDone('回答'); markPlanDone('分析');
              }
              if (data.type === 'tool') {
                const st = data.status;
                if (st === 'done') {
                  if (data.tool === 'retrieve') markPlanDone('检索');
                  else if (data.tool === 'search_papers') markPlanDone('搜索');
                  else if (data.tool === 'read_paper') markPlanDone('阅读');
                }
              }
              if (data.type === 'tool' && data.status === 'file_saved') {
                setWsRefresh(prev => prev + 1);
              }
              updateMsg(currentSections, replySection?.text || '');
            }
            if (data.type === 'citations' && data.papers) {
              setMessages(prev => prev.map(m => m.id === msgId ? { ...m, citations: data.papers } : m));
            }
          } catch {}
        }
      }
    }).catch(() => {
      setMessages(prev => prev.map(m => m.id === msgId ? { ...m, text: '请求失败，请检查 API 配置和网络连接。' } : m));
    }).finally(() => setLoading(false));
  }, [apiConfig, setMessages]);

  const handleNewChat = useCallback(async () => {
    const workspace = localStorage.getItem('pp-last-workspace') || DEFAULT_WORKSPACE;
    try {
      const res = await fetch(`/api/chats?workspace=${encodeURIComponent(workspace)}&title=${encodeURIComponent('新对话')}`, { method: 'POST' });
      const chat: ChatInfo = await res.json();
      setChatMetas(prev => [...prev, chat]);
      setCurrentChatId(chat.chat_id);
      setAllMessages(prev => ({ ...prev, [chat.chat_id]: [] }));
    } catch {}
  }, []);

  const handleCloseChat = useCallback(async (id: string) => {
    try { await fetch(`/api/chats/${encodeURIComponent(id)}`, { method: 'DELETE' }); } catch {}
    setAllMessages(prev => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    const remaining = chatMetas.filter(c => c.chat_id !== id);
    setChatMetas(remaining);
    if (id === currentChatId && remaining.length > 0) {
      setCurrentChatId(remaining[0].chat_id);
    }
  }, [currentChatId, chatMetas]);

  const handlePickWorkspace = useCallback(() => {
    const select = (path: string) => {
      if (path && currentChatId) {
        setChatMetas(prev => prev.map(c => c.chat_id === currentChatId ? { ...c, workspace_dir: path } : c));
        localStorage.setItem('pp-last-workspace', path);
      }
    };
    const w = window as any;
    if (w.pywebview?.api?.pick_folder) {
      w.pywebview.api.pick_folder().then((path: string) => {
        if (path) select(path);
        else {
          const manual = prompt('输入工作区的完整路径\n例如: D:\\research\\my-project');
          if (manual) select(manual.trim());
        }
      }).catch(() => {});
      return;
    }
    const manual = prompt('输入工作区的完整路径\n例如: D:\\research\\my-project 或 /home/user/project');
    if (manual) select(manual.trim());
  }, [currentChatId]);

  const handleSelectChat = useCallback((id: string) => {
    setCurrentChatId(id);
    setSidebarOpen(false);
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

  const handleUpdateMessageSections = useCallback((msgId: string, sections: MessageSection[]) => {
    setMessages(prev => prev.map(m => m.id === msgId ? { ...m, sections } : m));
  }, [setMessages]);

  return (
    <ErrorBoundary>
    <div className="app" onClick={(e) => {
        const target = e.target as HTMLElement;
        if (target.classList.contains('citation-link')) {
          const pid = target.getAttribute('data-pid');
          const title = target.getAttribute('title') || '';
          const pidVal = pid || '';
          for (const chatMsgs of Object.values(allMessages)) {
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
        chatTitle={activeChatTitle}
        onChatTitleChange={handleChatTitleChange}
        onNewChat={handleNewChat}
        workspacePath={activeChatWorkspace}
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
          if (confirm('确定清空当前对话？')) {
            setAllMessages(prev => ({ ...prev, [currentChatId]: [] }));
          }
        }}
      />
      <ChatTabs
        chats={chatMetas.map(c => ({ id: c.chat_id, title: c.title }))}
        activeId={currentChatId}
        onSelect={id => setCurrentChatId(id)}
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
            workspacePath={activeChatWorkspace}
            onUpdateMessageSections={handleUpdateMessageSections}
          />
          <ChatInput onSend={handleSend} disabled={loading} workspaceDir={activeChatWorkspace} />
        </div>
        <WorkspaceSidebar
          workspacePath={activeChatWorkspace}
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
        chats={chatMetas}
        currentChatId={currentChatId}
        onSelectChat={handleSelectChat}
        onNewChat={handleNewChat}
        onDeleteChat={handleCloseChat}
        onSetChatWorkspace={handlePickWorkspace}
      />

      <FloatingWindow
        isOpen={papersOpen}
        onToggle={() => setPapersOpen(false)}
        title="论文库"
        defaultWidth={420} defaultHeight={500}
        defaultTop={80} defaultLeft={360}
      >
        <PaperLibrary workspacePath={activeChatWorkspace} />
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

      <FloatingWindow
        isOpen={skillsOpen}
        onToggle={() => setSkillsOpen(false)}
        title="Skills"
        defaultWidth={360} defaultHeight={400}
        defaultTop={80} defaultLeft={520}
      >
        <SkillsPanel />
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

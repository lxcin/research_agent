export interface ToolCall {
  tool: string;
  status: string;
  query?: string;
  count?: number;
  papers?: { title: string; year: number }[];
  chunks?: number;
  error?: string;
  hint?: string;
}

export interface Citation {
  id: string;
  title: string;
  authors?: string[];
  year?: number;
  abstract?: string;
  doi?: string;
}

export interface ChatInfo {
  chat_id: string;
  title: string;
  created_at: string;
  turn_count: number;
  workspace_dir?: string;
}

export interface PlanItem {
  id: string;
  text: string;
  done: boolean;
}

export interface ThinkingSection { type: 'thinking'; text: string }
export interface PlanSection { type: 'plan'; items: PlanItem[] }
export interface ToolSection {
  type: 'tool';
  id: string;
  name: string;
  input: Record<string, any>;
  output?: Record<string, any>;
  status: 'running' | 'success' | 'error';
  fileChange?: { path: string; action: string; diff: string }
}
export interface ReplySection { type: 'reply'; text: string }
export type MessageSection = ThinkingSection | PlanSection | ToolSection | ReplySection;

export interface Message {
  id: string;
  role: 'user' | 'ai';
  text: string;
  timestamp: number;
  workspace?: string;
  toolCalls?: ToolCall[];
  citations?: Citation[];
  sections?: MessageSection[];
}

export interface GraphNode {
  id: string;
  label: string;
  type: 'paper' | 'viewpoint';
  color?: string;
  meta: string;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  type: 'supports' | 'contradicts' | 'extends';
}

export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface TreeNode {
  label: string;
  type?: string;
  children?: TreeNode[];
}

export interface PaperTree {
  title: string;
  root: TreeNode;
}

export interface Project {
  id: string;
  name: string;
  workspace_dir: string;
  status: 'active' | 'paused' | 'done';
}

export type WindowType = 'graph' | 'project' | null;

export interface ApiConfig {
  provider: string;
  apiKey: string;
  baseUrl: string;
  model: string;
}
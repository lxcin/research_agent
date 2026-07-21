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

export interface Message {
  id: string;
  role: 'user' | 'ai';
  text: string;
  timestamp: number;
  projectId?: string;
  toolCalls?: ToolCall[];
  citations?: Citation[];
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

export interface ProjectStep {
  name: string;
  status: 'done' | 'current' | 'pending';
  time: string;
}

export interface Project {
  id: string;
  name: string;
  status: 'active' | 'paused' | 'done';
  updated: string;
  created: string;
  summary: string;
  progress: number;
  steps: ProjectStep[];
}

export type WindowType = 'graph' | 'project' | null;

export interface ApiConfig {
  provider: string;
  apiKey: string;
  baseUrl: string;
  model: string;
}
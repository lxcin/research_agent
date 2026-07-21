import { useState } from 'react';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message, Citation } from '../types';
import type { PlanItem } from './PlanPanel';
import MermaidBlock from './MermaidBlock';

function extractText(children: ReactNode): string {
  if (typeof children === 'string') return children;
  if (typeof children === 'number') return String(children);
  if (Array.isArray(children)) return children.map(extractText).join('');
  if (children && typeof children === 'object' && 'props' in children) {
    return extractText((children as any).props.children);
  }
  return '';
}

interface ChatAreaProps {
  messages: Message[];
  welcome?: boolean;
  onSuggestionClick?: (prompt: string) => void;
  planItems?: PlanItem[];
  onTogglePlanItem?: (id: string) => void;
  onClearPlan?: () => void;
}

export default function ChatArea({ messages, welcome, onSuggestionClick, planItems, onTogglePlanItem, onClearPlan }: ChatAreaProps) {
  const done = planItems?.filter(i => i.done).length || 0;
  const total = planItems?.length || 0;

  return (
    <main className="chat-area">
      <div className="chat-scroll">
        {welcome && messages.length === 0 && (
          <div className="welcome">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"><path d="M12 2a10 10 0 0 1 10 10c0 5-4 9-10 9a9.9 9.9 0 0 1-5-1.5L2 22l1.5-5A9.9 9.9 0 0 1 2 12 10 10 0 0 1 12 2z"/><path d="M8 9h8"/><path d="M8 13h6"/></svg>
            <h2>PaperPilot 科研助手</h2>
            <p>你的 AI 研究伙伴，帮你检索、理解、综合文献，随时追问。</p>
            <div className="suggestions">
              <button onClick={() => onSuggestionClick?.('总结最近一篇关于量子纠错的论文')}>量子纠错综述</button>
              <button onClick={() => onSuggestionClick?.('比较Transformer和图神经网络在分子建模中的表现')}>GNN vs Transformer</button>
              <button onClick={() => onSuggestionClick?.('帮我搜索注意力机制的最新变体')}>注意力机制进展</button>
              <button onClick={() => onSuggestionClick?.('RLHF 2025 年的主要突破有哪些？')}>RLHF 突破</button>
            </div>
          </div>
        )}
        {messages.map(msg => (
          <div key={msg.id} className={`message ${msg.role}`}>
            {msg.text && (
              <div className="bubble">
                {msg.role === 'ai' ? (
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    components={{
                      a: ({ href, children }) => {
                        const text = extractText(children);
                        if (text.match(/^\[\d+\]$/) && msg.citations) {
                          const idx = parseInt(text.replace(/[\[\]]/g, '')) - 1;
                          const paper = msg.citations[idx];
                          if (paper) {
                            return (
                              <span className="citation-link" data-pid={paper.id} title={paper.title}>
                                {text}
                              </span>
                            );
                          }
                        }
                        return <a href={href} target="_blank" rel="noopener">{children}</a>;
                      },
                      img: ({ src, alt }) => {
                        return <img src={src} alt={alt || ''} className="chat-img" loading="lazy"
                                    style={{maxWidth:'100%',borderRadius:'8px',margin:'8px 0'}} />;
                      },
                      code: ({ className, children, ...props }) => {
                        const codeStr = extractText(children);
                        const match = /language-(\w+)/.exec(className || '');
                        if (match && match[1] === 'mermaid') {
                            return <MermaidBlock chart={codeStr} />;
                        }
                        const isInline = !className;
                        return isInline
                          ? <code className="inline-code" {...props}>{children}</code>
                          : <pre><code className={className} {...props}>{children}</code></pre>;
                      },
                    }}
                  >
                    {msg.text}
                  </ReactMarkdown>
                ) : (
                  msg.text
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {total > 0 && (
        <div className="plan-bar">
          <div className="plan-bar-header">
            <span className="plan-bar-title">执行计划 ({done}/{total})</span>
            <button className="plan-bar-clear" onClick={onClearPlan}>清除已完成</button>
          </div>
          <div className="plan-bar-list">
            {(planItems || []).map(item => (
              <div key={item.id} className={`plan-bar-item${item.done ? ' done' : ''}`}
                   onClick={() => onTogglePlanItem?.(item.id)}>
                <div className={`plan-bar-cb${item.done ? ' checked' : ''}`}>
                  {item.done && <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M20 6L9 17l-5-5"/></svg>}
                </div>
                <span>{item.text}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </main>
  );
}

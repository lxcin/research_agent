import { useState } from 'react';
import type { ToolSection } from '../types';

function getToolIcon(name: string): string {
  if (/search|retrieve|query/i.test(name)) return '\u{1F50D}';
  if (/file_write|write/i.test(name)) return '\u2699\uFE0F';
  if (/file_edit|edit/i.test(name)) return '\u270F\uFE0F';
  if (/file_read|read|open/i.test(name)) return '\uD83D\uDCC4';
  if (/shell|exec|bash|cmd/i.test(name)) return '\uD83D\uDCBB';
  if (/web|fetch|http|url|download/i.test(name)) return '\uD83C\uDF10';
  if (/python|py |run/i.test(name)) return '\uD83D\uDC0D';
  return '\uD83D\uDD27';
}

function inputSummary(input: Record<string, any>): string {
  const keys = Object.keys(input);
  if (keys.length === 0) return '';
  const first = keys[0];
  const val = input[first];
  if (typeof val === 'string') {
    const s = val.length > 50 ? val.slice(0, 50) + '...' : val;
    return `${first}: ${s}`;
  }
  return `${first}: ${JSON.stringify(val).slice(0, 50)}`;
}

function statusIcon(status: string): string {
  if (status === 'running') return '\u23F3';
  if (status === 'error') return '\u274C';
  return '\u2705';
}

function statusClass(status: string): string {
  if (status === 'running') return 'running';
  if (status === 'error') return 'error';
  return 'success';
}

export default function ToolCallBlock({ section }: { section: ToolSection }) {
  const [expanded, setExpanded] = useState(section.status === 'error');
  const icon = getToolIcon(section.name);
  const summary = inputSummary(section.input);
  const sClass = statusClass(section.status);
  const sIcon = statusIcon(section.status);
  const fcAction = section.fileChange?.action || '';
  const fileChangeClass = fcAction === 'create' ? 'file-change-create' : fcAction === 'edit' ? 'file-change-edit' : '';

  return (
    <div className={`tool-block ${sClass} ${fileChangeClass}`}>
      <div className="tool-block-header" onClick={() => setExpanded(!expanded)}>
        <span className="tool-block-icon">{icon}</span>
        <span className="tool-block-name">{section.name}</span>
        <span className="tool-block-summary">{summary}</span>
        <span className="tool-block-status">{sIcon}</span>
        <span className="tool-block-caret">{expanded ? '\u25BC' : '\u25B6'}</span>
      </div>
      {expanded && (
        <div className="tool-block-body">
          {Object.keys(section.input).length > 0 && (
            <div className="tool-block-row">
              <span className="tool-block-label">input</span>
              <pre className="tool-block-pre">{JSON.stringify(section.input, null, 2)}</pre>
            </div>
          )}
          {section.output !== undefined && (
            <div className="tool-block-row">
              <span className="tool-block-label">{section.status === 'error' ? 'error' : 'output'}</span>
              <pre className={`tool-block-pre ${section.status === 'error' ? 'tool-block-error' : ''}`}>
                {typeof section.output === 'string' ? section.output : JSON.stringify(section.output, null, 2)}
              </pre>
            </div>
          )}
          {section.fileChange && (
            <div className="tool-diff">
              <div className="tool-diff-header">
                {section.fileChange.action} {section.fileChange.path}
              </div>
              <pre className="tool-diff-content">
                {section.fileChange.diff.split('\n').map((line, i) => {
                  let cls = '';
                  if (line.startsWith('+')) cls = 'diff-add';
                  else if (line.startsWith('-')) cls = 'diff-rem';
                  else if (line.startsWith('@@')) cls = 'diff-hunk';
                  return <div key={i} className={`diff-line ${cls}`}>{line}</div>;
                })}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

import { useState } from 'react';

export default function ThinkingBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="thinking-block">
      <div className="thinking-block-header" onClick={() => setExpanded(!expanded)}>
        <span className="thinking-block-icon">{'\uD83D\uDCAD'}</span>
        <span className="thinking-block-title">思考</span>
        <span className="thinking-block-caret">{expanded ? '\u25BC' : '\u25B6'}</span>
      </div>
      {expanded && (
        <div className="thinking-block-body">{text}</div>
      )}
    </div>
  );
}

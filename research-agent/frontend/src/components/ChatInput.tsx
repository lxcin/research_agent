import { useRef, useCallback, KeyboardEvent, useState } from 'react';

interface ChatInputProps {
  onSend: (text: string) => void;
  disabled?: boolean;
}

export default function ChatInput({ onSend, disabled }: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  const handleSend = useCallback(() => {
    const text = textareaRef.current?.value.trim();
    if (!text) return;
    onSend(text);
    if (textareaRef.current) {
      textareaRef.current.value = '';
      textareaRef.current.style.height = 'auto';
    }
  }, [onSend]);

  const handleKeyDown = useCallback((e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (el) { el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px'; }
  }, []);

  const handleUpload = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const ext = file.name.split('.').pop()?.toLowerCase() || '';
    if (!['pdf', 'md', 'txt'].includes(ext)) return;
    setUploading(true);
    const form = new FormData();
    form.append('file', file);
    try {
      const r = await fetch('/api/upload/pdf', { method: 'POST', body: form });
      const data = await r.json();
      if (data.status === 'ok') {
        onSend(`阅读这篇论文: ${data.title}`);
      }
    } catch {} finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  }, [onSend]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    // Support paste PDF from clipboard in future
  }, []);

  return (
    <div className="input-bar">
      <div className="input-row">
        <div className="input-wrap">
          <textarea
            ref={textareaRef}
            rows={1}
            placeholder={uploading ? '正在上传 PDF...' : '输入科研问题，或上传 PDF…'}
            disabled={disabled || uploading}
            onKeyDown={handleKeyDown}
            onInput={handleInput}
            onPaste={handlePaste}
          />
          <input ref={fileRef} type="file" accept=".pdf,.md,.txt" style={{ display: 'none' }}
                 onChange={handleUpload} />
          <button className="attach-btn" title="上传 PDF" onClick={() => fileRef.current?.click()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"/></svg>
          </button>
        </div>
        <button className="send-btn" onClick={handleSend} disabled={disabled} title="发送">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4 20-7z"/></svg>
        </button>
      </div>
    </div>
  );
}
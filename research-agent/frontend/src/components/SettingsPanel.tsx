import { useState, useEffect } from 'react';
import type { ApiConfig } from '../types';

interface SettingsPanelProps {
  onClose: () => void;
  config: ApiConfig;
  onSave: (config: ApiConfig) => void;
}

const PROVIDERS = [
  { value: 'deepseek', label: 'DeepSeek', baseUrl: 'https://api.deepseek.com/v1' },
  { value: 'openai', label: 'OpenAI', baseUrl: 'https://api.openai.com/v1' },
  { value: 'anthropic', label: 'Anthropic', baseUrl: 'https://api.anthropic.com/v1' },
  { value: 'openai_compatible', label: 'OpenAI 兼容', baseUrl: '' },
];

export default function SettingsPanel({ onClose, config, onSave }: SettingsPanelProps) {
  const [provider, setProvider] = useState(config.provider);
  const [apiKey, setApiKey] = useState(config.apiKey);
  const [baseUrl, setBaseUrl] = useState(config.baseUrl);
  const [model, setModel] = useState(config.model);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const handleProviderChange = (p: string) => {
    setProvider(p);
    const preset = PROVIDERS.find(x => x.value === p);
    if (preset && preset.baseUrl) setBaseUrl(preset.baseUrl);
    if (p === 'deepseek') setModel('deepseek-chat');
    else if (p === 'openai') setModel('gpt-4o');
    else if (p === 'anthropic') setModel('claude-3-haiku-20240307');
  };

  const handleSave = () => {
    onSave({ provider, apiKey, baseUrl, model });
    setSaved(true);
    setTimeout(() => setSaved(false), 1500);
  };

  return (
    <div className="settings-overlay" onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="settings-panel">
        <div className="settings-header">
          <span className="settings-title">API 设置</span>
          <button className="settings-close" onClick={onClose}>✕</button>
        </div>
        <div className="settings-body">
          <div className="settings-field">
            <label>提供商</label>
            <div className="settings-provider-group">
              {PROVIDERS.map(p => (
                <button
                  key={p.value}
                  className={`settings-provider-btn${provider === p.value ? ' active' : ''}`}
                  onClick={() => handleProviderChange(p.value)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>
          <div className="settings-field">
            <label>API Key</label>
            <div className="settings-input-wrap">
              <input
                type="password"
                value={apiKey}
                onChange={e => setApiKey(e.target.value)}
                placeholder="sk-..."
              />
              <button
                className="settings-toggle-vis"
                onClick={() => {
                  const el = document.querySelector('.settings-input-wrap input') as HTMLInputElement;
                  if (el) el.type = el.type === 'password' ? 'text' : 'password';
                }}
              >
                <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
              </button>
            </div>
          </div>
          <div className="settings-field">
            <label>Base URL</label>
            <input
              type="text"
              value={baseUrl}
              onChange={e => setBaseUrl(e.target.value)}
              placeholder="https://api.openai.com/v1"
              className="settings-input"
            />
          </div>
          <div className="settings-field">
            <label>模型</label>
            <input
              type="text"
              value={model}
              onChange={e => setModel(e.target.value)}
              placeholder="deepseek-chat"
              className="settings-input"
            />
          </div>
        </div>
        <div className="settings-footer">
          <span className="settings-hint">
            配置保存在浏览器本地，不会上传到服务器。
          </span>
          <button className="settings-save-btn" onClick={handleSave}>
            {saved ? '✓ 已保存' : '保存'}
          </button>
        </div>
      </div>
    </div>
  );
}
import { useState, useEffect } from 'react';

interface Skill {
    name: string;
    description: string;
    triggers: string[];
    enabled: boolean;
    file_path: string;
}

export default function SkillsPanel() {
    const [skills, setSkills] = useState<Skill[]>([]);
    const [editing, setEditing] = useState<Skill | null>(null);
    const [body, setBody] = useState('');
    const [triggers, setTriggers] = useState('');
    const [desc, setDesc] = useState('');

    const load = () => {
        fetch('/api/skills').then(r => r.json()).then(setSkills).catch(() => {});
    };

    useEffect(() => { load(); }, []);

    const handleToggle = (s: Skill) => {
        fetch(`/api/skills/${s.name}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ...s, enabled: !s.enabled }),
        }).then(() => load());
    };

    const handleEdit = (s: Skill) => {
        setEditing(s);
        setDesc(s.description);
        setTriggers(s.triggers.join(', '));
        fetch(`/api/project-files/_skills_/${s.name}.md`)
            .then(r => r.text())
            .then(t => { const m = t.match(/^---[\s\S]*?---\n([\s\S]*)/); setBody(m ? m[1].trim() : ''); })
            .catch(() => setBody(''));
    };

    const handleSave = () => {
        if (!editing) return;
        fetch(`/api/skills/${editing.name}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                description: desc,
                triggers: triggers.split(',').map(t => t.trim()).filter(Boolean),
                enabled: editing.enabled,
                body,
            }),
        }).then(() => { setEditing(null); load(); });
    };

    return (
        <div className="skills-panel">
            <div className="sk-header">
                <h3>Skills ({skills.length})</h3>
            </div>
            <div className="sk-list">
                {skills.map(s => (
                    <div key={s.name} className={`sk-item${s.enabled ? '' : ' sk-disabled'}`}>
                        <div className="sk-item-top">
                            <span className="sk-name">{s.name}</span>
                            <div className="sk-actions">
                                <button className="sk-toggle" onClick={() => handleToggle(s)}>
                                    {s.enabled ? 'ON' : 'OFF'}
                                </button>
                                <button className="sk-edit-btn" onClick={() => handleEdit(s)}>编辑</button>
                            </div>
                        </div>
                        <div className="sk-desc">{s.description}</div>
                        {s.triggers.length > 0 && (
                            <div className="sk-triggers">{s.triggers.join(', ')}</div>
                        )}
                    </div>
                ))}
            </div>
            {editing && (
                <div className="sk-editor">
                    <div className="sk-editor-header">
                        <span>编辑: {editing.name}</span>
                        <button onClick={() => setEditing(null)}>✕</button>
                    </div>
                    <input value={desc} onChange={e => setDesc(e.target.value)} placeholder="描述" />
                    <input value={triggers} onChange={e => setTriggers(e.target.value)} placeholder="触发词（逗号分隔）" />
                    <textarea value={body} onChange={e => setBody(e.target.value)} rows={10} placeholder="Skill 内容（Markdown）" />
                    <button className="sk-save-btn" onClick={handleSave}>保存</button>
                </div>
            )}
        </div>
    );
}
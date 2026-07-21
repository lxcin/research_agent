# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for PaperPilot Desktop."""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ['desktop.py'],
    pathex=['E:/PROJ/research-agent'],
    binaries=[],
    datas=[
        ('E:/PROJ/research-agent/frontend/dist', 'frontend/dist'),
        ('E:/PROJ/research-agent/src/research_agent', 'src/research_agent'),
    ],
    hiddenimports=[
        'uvicorn', 'uvicorn.loops', 'uvicorn.loops.auto',
        'uvicorn.protocols', 'uvicorn.protocols.http',
        'fastapi', 'fastapi.middleware',
        'litellm', 'chromadb', 'jieba', 'rank_bm25',
        'pymupdf', 'tiktoken', 'networkx',
        'research_agent', 'research_agent.agent',
        'research_agent.server', 'research_agent.tools',
        'research_agent.tools.builtin', 'research_agent.tools.builtin.retrieve',
        'research_agent.tools.builtin.filesystem',
        'research_agent.retrieval', 'research_agent.ingestion',
        'research_agent.search', 'research_agent.store',
        'research_agent.context', 'research_agent.memory',
        'research_agent.knowledge_graph',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PaperPilot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
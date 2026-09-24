"""Quality report artifacts: JSON (machine) + self-contained HTML dashboard (human).

The dashboard is a single dependency-free HTML file (inline CSS + SVG) written to
`data_dir/quality/quality-latest.html`; every run also leaves `quality-{ts}.json`
for history/trends and API consumption.
"""
from __future__ import annotations

import glob
import html
import json
import os
from datetime import datetime, timezone

from research_agent.config import get_data_dir


def _quality_dir(out_dir: str | None = None) -> str:
    return out_dir or os.path.join(str(get_data_dir()), "quality")


def load_history(out_dir: str | None = None, limit: int = 40) -> list[dict]:
    base = _quality_dir(out_dir)
    files = sorted(glob.glob(os.path.join(base, "quality-*.json")))
    history: list[dict] = []
    for path in files[-limit:]:
        try:
            with open(path, encoding="utf-8") as fh:
                history.append(json.load(fh))
        except Exception:
            continue
    return history


def get_latest_report(out_dir: str | None = None) -> dict | None:
    hist = load_history(out_dir, limit=1)
    return hist[-1] if hist else None


def _sparkline(values: list[float], color: str, height: int = 34) -> str:
    vals = [v for v in values if v is not None]
    if len(vals) < 2:
        return "<span class='muted'>需要至少 2 次运行</span>"
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    w = 180
    step = w / (len(vals) - 1)
    pts = []
    for i, v in enumerate(vals):
        x = round(i * step, 1)
        y = round(height - (v - lo) / span * (height - 6) - 3, 1)
        pts.append(f"{x},{y}")
    return (f"<svg width='{w}' height='{height}' viewBox='0 0 {w} {height}'>"
            f"<polyline fill='none' stroke='{color}' stroke-width='2' points='{';'.join(pts)}'/>"
            f"</svg>")


def _bar(value: float | None, threshold: float | None, unit: str = "%") -> str:
    if value is None:
        return "<span class='muted'>n/a</span>"
    pct = max(0.0, min(100.0, float(value)))
    cls = "ok" if (threshold is None or value >= threshold) else "bad"
    return (f"<div class='bar'><div class='fill {cls}' style='width:{pct:.1f}%'></div>"
            f"<span class='bar-txt'>{value:g}{unit}</span></div>")


_RT_DIM_ZH = {"tool_health": "工具健康", "convergence": "收敛/循环",
              "completion": "任务完成", "efficiency": "效率", "stability": "稳定性"}


def render_html(report: dict, history: list[dict] | None = None) -> str:
    history = history or [report]
    e = html.escape
    overall = report.get("overall", "fail")
    badge_cls = "ok" if overall == "pass" else "bad"
    tests = report.get("tests", {})
    s = report.get("summary", {})

    rows = []
    for c in report.get("checks", []):
        mark = "PASS" if c["passed"] else "FAIL"
        mcls = "ok" if c["passed"] else "bad"
        val = "n/a" if c["value"] is None else f"{c['value']}{c.get('unit', '')}"
        thr = "—" if c["threshold"] is None else f"≥ {c['threshold']}{c.get('unit', '')}"
        rows.append(
            f"<tr><td><span class='pill {mcls}'>{mark}</span></td>"
            f"<td>{e(c['label'])}</td><td class='cat'>{e(c['category'])}</td>"
            f"<td class='num'>{e(str(val))}</td><td class='num'>{e(thr)}</td>"
            f"<td class='detail'>{e(c.get('detail', ''))}</td></tr>")

    cov_rows = []
    for suffix, minpct in (report.get("thresholds", {}).get("coverage_min") or {}).items():
        val = next((v for k, v in (report.get("coverage_files") or {}).items()
                    if k.replace("\\", "/").endswith(suffix)), None)
        cov_rows.append(f"<tr><td>{e(suffix)}</td><td>{_bar(val, minpct)}</td>"
                        f"<td class='num'>{'≥ %g%%' % minpct}</td></tr>")

    hist_rows = []
    for h in history[-12:]:
        ht = h.get("tests", {})
        hs = h.get("summary") or {}
        hcls = "ok" if h.get("overall") == "pass" else "bad"
        cov_txt = ("%g%%" % h["coverage_total"]) if h.get("coverage_total") is not None else "n/a"
        gate_txt = f"{hs.get('passed', 0)}/{hs.get('total', 0)}"
        rt_o = (h.get("runtime") or {}).get("overall")
        rt_txt = ("%g" % rt_o) if rt_o is not None else "n/a"
        htm = h.get("telemetry") or {}
        tm_txt = ("$%g" % htm.get("cost_usd", 0)) if htm.get("runs") else "n/a"
        hist_rows.append(
            f"<tr><td class='num'>{e(h.get('ts', ''))}</td>"
            f"<td><span class='pill {hcls}'>{e(h.get('overall', '?'))}</span></td>"
            f"<td class='num'>{ht.get('passed', 0)}/{ht.get('total', 0)}</td>"
            f"<td class='num'>{ht.get('failed', 0)}</td>"
            f"<td class='num'>{cov_txt}</td>"
            f"<td class='num'>{gate_txt}</td>"
            f"<td class='num'>{rt_txt}</td>"
            f"<td class='num'>{tm_txt}</td></tr>")

    passed_series = [h.get("tests", {}).get("passed") for h in history]
    cov_series = [h.get("coverage_total") for h in history]
    rt_series = [(h.get("runtime") or {}).get("overall") for h in history]

    tm = report.get("telemetry") or {}
    tm_runs = tm.get("runs", 0)
    tm_cache_txt = ("%.1f%%" % (tm["cache_hit_rate"] * 100)
                    if tm.get("cache_hit_rate") is not None else "n/a(未上报)")
    tm_series = [(h.get("telemetry") or {}).get("cost_usd") for h in history]

    rt = report.get("runtime") or {}
    rt_dims = rt.get("dimensions") or {}
    rt_overall = rt.get("overall")
    rt_grade = rt.get("grade", "-")
    rt_dim_rows = "".join(
        f"<tr><td>{e(_RT_DIM_ZH.get(k, k))}</td><td>{_bar(v, None)}</td></tr>"
        for k, v in rt_dims.items()) or "<tr><td colspan=2 class=muted>无运行时数据</td></tr>"
    rt_issue_rows = "".join(
        f"<tr><td>{e(k)}</td><td class='num'>{c}</td></tr>"
        for k, c in sorted((rt.get("issues") or {}).items(), key=lambda kv: -kv[1])
    ) or "<tr><td colspan=2 class=muted>无</td></tr>"

    return f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PaperPilot 质量看板</title>
<style>
:root{{--bg:#0f1420;--card:#161d2e;--fg:#e6ebf5;--muted:#8b96ac;--ok:#31c48d;--bad:#f05252;--line:#26304a;--accent:#4f8cff}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 "Segoe UI",system-ui,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:20px;margin:0 0 4px}}
.muted{{color:var(--muted)}}
.badge{{display:inline-block;padding:4px 14px;border-radius:999px;font-weight:700;letter-spacing:.5px}}
.badge.ok{{background:rgba(49,196,141,.15);color:var(--ok);border:1px solid var(--ok)}}
.badge.bad{{background:rgba(240,82,82,.15);color:var(--bad);border:1px solid var(--bad)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:20px 0}}
.kpi{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}}
.kpi .v{{font-size:26px;font-weight:700}}
.kpi .l{{color:var(--muted);font-size:12px;margin-top:2px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:16px 0}}
.card h2{{font-size:14px;margin:0 0 12px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.6px}}
table{{width:100%;border-collapse:collapse}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line)}}
th{{color:var(--muted);font-weight:600;font-size:12px}}
td.num{{text-align:right;font-variant-numeric:tabular-nums}}
td.cat{{color:var(--muted)}}
td.detail{{color:var(--muted);font-size:12px}}
.pill{{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:700}}
.pill.ok{{background:rgba(49,196,141,.15);color:var(--ok)}}
.pill.bad{{background:rgba(240,82,82,.15);color:var(--bad)}}
.bar{{position:relative;height:18px;background:#0c1120;border-radius:9px;overflow:hidden;min-width:140px}}
.fill{{position:absolute;left:0;top:0;bottom:0;border-radius:9px}}
.fill.ok{{background:linear-gradient(90deg,#2a9d74,#31c48d)}}
.fill.bad{{background:linear-gradient(90deg,#b23b3b,#f05252)}}
.bar-txt{{position:relative;padding-left:8px;font-size:12px;line-height:18px}}
.trends{{display:flex;gap:36px;flex-wrap:wrap}}
.trend .t{{color:var(--muted);font-size:12px;margin-bottom:4px}}
.foot{{color:var(--muted);font-size:12px;margin-top:24px}}
</style></head><body><div class="wrap">
<h1>PaperPilot 质量看板 <span class="badge {badge_cls}">{e(overall.upper())}</span></h1>
<div class="muted">commit {e(report.get('commit') or '-')} · {e(report.get('generated_at', ''))}</div>

<div class="kpis">
  <div class="kpi"><div class="v">{s.get('passed', 0)}/{s.get('total', 0)}</div><div class="l">门禁通过</div></div>
  <div class="kpi"><div class="v">{tests.get('passed', 0)}</div><div class="l">测试通过</div></div>
  <div class="kpi"><div class="v" style="color:{'var(--bad)' if tests.get('failed') else 'var(--ok)'}">{tests.get('failed', 0)}</div><div class="l">测试失败</div></div>
  <div class="kpi"><div class="v">{tests.get('skipped', 0)}</div><div class="l">跳过</div></div>
  <div class="kpi"><div class="v">{'%g%%' % report['coverage_total'] if report.get('coverage_total') is not None else 'n/a'}</div><div class="l">总覆盖率</div></div>
  <div class="kpi"><div class="v">{('%g' % rt_overall) if rt_overall is not None else 'n/a'}</div><div class="l">运行时框架分 ({e(str(rt_grade))})</div></div>
  <div class="kpi"><div class="v">{('$%g' % tm.get('cost_usd', 0)) if tm_runs else 'n/a'}</div><div class="l">累计费用 ({tm_runs} 次)</div></div>
</div>

<div class="card"><h2>质量门禁</h2>
<table><thead><tr><th>状态</th><th>检查项</th><th>类别</th><th class="num">值</th><th class="num">阈值</th><th>说明</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>

<div class="card"><h2>关键模块覆盖率</h2>
<table><thead><tr><th>模块</th><th>覆盖</th><th class="num">阈值</th></tr></thead>
<tbody>{''.join(cov_rows) or '<tr><td colspan=3 class=muted>未采集</td></tr>'}</tbody></table></div>

<div class="card"><h2>运行时评测（成本/时延）</h2>
<table><thead><tr><th>指标</th><th class="num">总计</th><th class="num">均值</th><th class="num">P95</th></tr></thead>
<tbody>
<tr><td>token</td><td class="num">{tm.get('tokens', 0)}</td><td class="num">{(tm.get('avg') or {}).get('tokens', 0)}</td><td class="num">{(tm.get('p95') or {}).get('tokens', 0)}</td></tr>
<tr><td>费用 ($)</td><td class="num">{tm.get('cost_usd', 0)}</td><td class="num">{(tm.get('avg') or {}).get('cost_usd', 0)}</td><td class="num">{(tm.get('p95') or {}).get('cost_usd', 0)}</td></tr>
<tr><td>耗时 (ms)</td><td class="num">{tm.get('wall_ms', 0)}</td><td class="num">{(tm.get('avg') or {}).get('wall_ms', 0)}</td><td class="num">{(tm.get('p95') or {}).get('wall_ms', 0)}</td></tr>
</tbody></table>
<div class="muted" style="margin-top:8px">数据源 data_dir/usage/runs.jsonl · 完成 {tm.get('completed', 0)}/{tm_runs} · 缓存命中 {tm_cache_txt if tm_runs else '—'}</div></div>

<div class="card"><h2>运行时框架审计</h2>
<div class="muted">数据源 data_dir/logs/*.jsonl · 会话 {rt.get('sessions', 0)} · 工具 {rt.get('tool_calls', 0)}/失败 {rt.get('tool_errors', 0)} · 故障 {rt.get('faults', 0)} · 完成 {rt.get('completed', 0)}</div>
<table style="margin-top:10px"><thead><tr><th>维度</th><th>分数</th></tr></thead>
<tbody>{rt_dim_rows}</tbody></table>
<div class="muted" style="margin:10px 0 4px">高频问题</div>
<table><thead><tr><th>问题</th><th class="num">次数</th></tr></thead>
<tbody>{rt_issue_rows}</tbody></table></div>

<div class="card"><h2>趋势</h2><div class="trends">
  <div class="trend"><div class="t">测试通过数</div>{_sparkline(passed_series, '#4f8cff')}</div>
  <div class="trend"><div class="t">总覆盖率</div>{_sparkline(cov_series, '#31c48d')}</div>
  <div class="trend"><div class="t">运行时框架分</div>{_sparkline(rt_series, '#f0a020')}</div>
  <div class="trend"><div class="t">累计费用 ($)</div>{_sparkline(tm_series, '#f05252')}</div>
</div></div>

<div class="card"><h2>历史运行</h2>
<table><thead><tr><th class="num">时间</th><th>结果</th><th class="num">测试通过</th><th class="num">失败</th><th class="num">覆盖率</th><th class="num">门禁</th><th class="num">运行时</th><th class="num">费用</th></tr></thead>
<tbody>{''.join(hist_rows) or '<tr><td colspan=8 class=muted>无历史</td></tr>'}</tbody></table></div>

<div class="foot">生成于 {e(report.get('generated_at', ''))} · research-agent quality</div>
</div></body></html>"""


def write_report(report: dict, out_dir: str | None = None,
                 history_limit: int = 40) -> dict:
    """Write quality-{ts}.json + quality-latest.html (+ timestamped html)."""
    base = _quality_dir(out_dir)
    os.makedirs(base, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report = {**report, "ts": ts}

    json_path = os.path.join(base, f"quality-{ts}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2)

    history = load_history(base, history_limit)
    if not any(h.get("ts") == ts for h in history):
        history.append(report)

    html_doc = render_html(report, history)
    html_path = os.path.join(base, "quality-latest.html")
    with open(html_path, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    with open(os.path.join(base, f"quality-{ts}.html"), "w", encoding="utf-8") as fh:
        fh.write(html_doc)

    return {"json_path": json_path, "html_path": html_path}

"""Interactive single-file HTML Dashboard Generator for DiffQA QA audits."""

import base64
from pathlib import Path
from typing import List, Optional
from diffqa.analyzer.flow_planner import TestFlowPlan
from diffqa.judge.superhuman import SuperhumanAuditReport
from diffqa.reporter.markdown import AUTOMATIC_CHECKS, FlowReportItem, reproduction_note

MAX_EMBEDDED_PNG = 2_000_000  # keep the single-file dashboard openable

DASHBOARD_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>DiffQA Autonomous QA Audit Dashboard</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --border: #1f2937;
      --text: #f9fafb;
      --text-muted: #9ca3af;
      --primary: #3b82f6;
      --success: #10b981;
      --danger: #ef4444;
      --warning: #f59e0b;
      --code-bg: #030712;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 2rem; }
    .container { max-width: 1200px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 1.5rem; border-bottom: 1px solid var(--border); margin-bottom: 2rem; }
    .logo-area { display: flex; align-items: center; gap: 0.75rem; }
    .logo-badge { background: linear-gradient(135deg, #2563eb, #7c3aed); color: white; padding: 0.35rem 0.75rem; border-radius: 6px; font-weight: 800; font-size: 1.1rem; }
    .header-meta { color: var(--text-muted); font-size: 0.9rem; }
    .grid-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 2rem; }
    .stat-card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; }
    .stat-val { font-size: 2rem; font-weight: 800; margin-top: 0.25rem; }
    .stat-label { font-size: 0.85rem; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.05em; }
    .val-success { color: var(--success); }
    .val-danger { color: var(--danger); }
    .val-primary { color: var(--primary); }
    .card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 8px; padding: 1.5rem; margin-bottom: 2rem; }
    .card-title { font-size: 1.25rem; font-weight: 700; margin-bottom: 1rem; display: flex; align-items: center; justify-content: space-between; }
    table { width: 100%; border-collapse: collapse; text-align: left; }
    th, td { padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); font-size: 0.9rem; }
    th { color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 0.75rem; }
    .badge { display: inline-block; padding: 0.2rem 0.5rem; border-radius: 4px; font-size: 0.75rem; font-weight: 700; }
    .badge-pass { background: rgba(16, 185, 129, 0.2); color: var(--success); border: 1px solid var(--success); }
    .badge-fail { background: rgba(239, 68, 68, 0.2); color: var(--danger); border: 1px solid var(--danger); }
    .badge-crit { background: #7f1d1d; color: #fca5a5; }
    .badge-high { background: #7c2d12; color: #fdba74; }
    .code-block { background: var(--code-bg); border: 1px solid var(--border); border-radius: 6px; padding: 1rem; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; overflow-x: auto; color: #93c5fd; }
    .btn-copy { background: #1f2937; color: white; border: 1px solid #374151; padding: 0.3rem 0.6rem; border-radius: 4px; font-size: 0.75rem; cursor: pointer; float: right; margin-bottom: 0.5rem; }
    .btn-copy:hover { background: #374151; }
    .issue-item { padding: 1rem 0; border-bottom: 1px solid var(--border); }
    .issue-item:last-child { border-bottom: none; }
    footer { text-align: center; color: var(--text-muted); font-size: 0.85rem; margin-top: 3rem; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div class="logo-area">
        <div class="logo-badge">DiffQA</div>
        <div>
          <h2>Autonomous QA Audit Dashboard</h2>
          <div class="header-meta">Target: <b>{{ preview_url }}</b> </div>
        </div>
      </div>
      <div>
        <span class="badge {{ 'badge-pass' if failed_count == 0 else 'badge-fail' }}" style="font-size: 1rem; padding: 0.5rem 1rem;">
          {{ 'ALL FLOWS PASSED' if failed_count == 0 else (failed_count|string + ' REGRESSIONS DETECTED') }}
        </span>
      </div>
    </header>

    <div class="grid-stats">
      <div class="stat-card">
        <div class="stat-label">Quality Score</div>
        <div class="stat-val {{ 'val-success' if quality_score > 80 else ('val-danger' if quality_score < 50 else 'val-primary') }}">
          {{ quality_score }}/100
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Test Flows Run</div>
        <div class="stat-val val-primary">{{ total_flows }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Regressions Caught</div>
        <div class="stat-val {{ 'val-danger' if failed_count > 0 else 'val-success' }}">{{ failed_count }}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Synthesized Playwright Tests</div>
        <div class="stat-val val-success">{{ specs_count }}</div>
      </div>
    </div>

    <!-- Test Flows Table -->
    <div class="card">
      <div class="card-title">Exploratory Test Journeys</div>
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Flow Title</th>
            <th>Type</th>
            <th>Route</th>
            <th>Duration</th>
          </tr>
        </thead>
        <tbody>
          {% for item in items %}
          <tr>
            <td>
              <span class="badge {{ 'badge-pass' if item.passed else 'badge-fail' }}">
                {{ 'PASS' if item.passed else ('NOT TESTED' if item.verdict.verdict == 'ERROR' else 'FAIL') }}
              </span>
            </td>
            <td><b>{{ item.title }}</b></td>
            <td><code>{{ item.flow_type }}</code></td>
            <td><code>{{ item.route }}</code></td>
            <td>{{ "%.1f"|format(item.duration_seconds) }}s</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>

    <!-- Regressions & Synthesized Specs -->
    {% if failed_count > 0 %}
    <div class="card" style="border-left: 4px solid var(--danger);">
      <div class="card-title" style="color: var(--danger);">🚨 Regressions & Auto-Synthesized Playwright Specs</div>
      {% for item in items %}
      {% if not item.passed %}
      <div style="margin-bottom: 2rem;">
        <h3 style="color: #fca5a5; margin-bottom: 0.5rem;">{{ item.verdict.title or item.title }}</h3>
        <p style="color: var(--text-muted); font-size: 0.9rem; margin-bottom: 0.75rem;">
          Severity: <span class="badge badge-crit">{{ item.verdict.severity.value if item.verdict.severity else 'HIGH' }}</span> |
          Confidence: <b>{{ (item.verdict.confidence * 100)|int }}%</b>
        </p>
        <p style="color: var(--text-muted); font-size: 0.9rem; margin-bottom: 0.75rem;">{{ reproduction_note(item.verdict) }}</p>
        {% if item.verdict.root_cause_hypothesis %}
        <p style="margin-bottom: 0.75rem;"><b>Root Cause Hypothesis:</b> {{ item.verdict.root_cause_hypothesis }}</p>
        {% endif %}
        {% if item.verdict.evidence_logs %}
        <p style="margin-bottom: 0.25rem;"><b>Evidence:</b></p>
        <ul style="margin: 0 0 0.75rem 1.25rem; color: var(--text-muted); font-size: 0.9rem;">
          {% for e in item.verdict.evidence_logs %}<li><code>{{ e }}</code></li>{% endfor %}
        </ul>
        {% endif %}
        {% if item.verdict.repro_steps %}
        <p style="margin-bottom: 0.25rem;"><b>Reproduction steps:</b></p>
        <ol style="margin: 0 0 0.75rem 1.25rem; font-size: 0.9rem;">
          {% for s in item.verdict.repro_steps %}<li>{{ s }}</li>{% endfor %}
        </ol>
        {% endif %}
        {% if screenshots.get(item.flow_id) %}
        <p style="margin-bottom: 0.25rem;"><b>Screenshot at failure:</b></p>
        <img src="data:image/png;base64,{{ screenshots[item.flow_id] }}" alt="Screenshot of {{ item.title }} at failure"
             style="max-width: 100%; border: 1px solid var(--border); border-radius: 6px; margin-bottom: 0.75rem;">
        {% endif %}

        {% if item.spec_code_ts %}
        <div style="margin-top: 1rem;">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 0.5rem;">
            <b>Executable Playwright Test (.spec.ts):</b>
            <button class="btn-copy" onclick="navigator.clipboard.writeText(document.getElementById('code-{{ loop.index }}').innerText); this.innerText='Copied!';">Copy Test</button>
          </div>
          <pre class="code-block" id="code-{{ loop.index }}"><code>{{ item.spec_code_ts }}</code></pre>
        </div>
        {% endif %}
      </div>
      {% endif %}
      {% endfor %}
    </div>
    {% endif %}

    <div class="card">
      <div class="card-title">Coverage: what this run checked</div>
      <table>
        <thead><tr><th>Flow</th><th>Steps</th><th>Assertions</th><th>Invariants checked</th></tr></thead>
        <tbody>
          {% for item in items %}
          <tr>
            <td><code>{{ item.flow_id }}</code></td>
            <td>{{ item.steps_count }}</td>
            <td>{{ item.assertions_count }}</td>
            <td>{% if item.invariants %}{{ item.invariants|join('; ') }}{% else %}—{% endif %}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
      <p style="color: var(--text-muted); font-size: 0.9rem; margin-top: 1rem;"><b>Automatic checks on every step:</b></p>
      <ul style="margin: 0.25rem 0 0 1.25rem; color: var(--text-muted); font-size: 0.9rem;">
        {% for c in automatic_checks %}<li>{{ c }}</li>{% endfor %}
      </ul>
    </div>

    <!-- Superhuman Invariant Audits -->
    {% if audit %}
    <div class="card">
      <div class="card-title">Superhuman Invariant & Health Audits</div>
      <p style="color: var(--text-muted); font-size: 0.9rem; margin-bottom: 1rem;">
        Audits covering layout shifts, accessibility (WCAG), broken assets, and network anomalies that evade standard tests.
      </p>
      {% for issue in audit.issues %}
      <div class="issue-item">
        <span class="badge {{ 'badge-crit' if issue.severity == 'CRITICAL' else ('badge-high' if issue.severity == 'HIGH' else 'badge-pass') }}">
          {{ issue.severity }}
        </span>
        <strong style="margin-left: 0.5rem;">[{{ issue.category }}]</strong> {{ issue.message }}
        {% if issue.selector %}<div style="color: var(--text-muted); font-size: 0.8rem; margin-top: 0.25rem;">Selector: <code>{{ issue.selector }}</code></div>{% endif %}
      </div>
      {% endfor %}
    </div>
    {% endif %}

    <footer>
      Generated autonomously by <b>DiffQA</b> · Powered by Meta Model API
    </footer>
  </div>
</body>
</html>
"""


class DashboardGenerator:
    """Generates self-contained, interactive single-file HTML reports."""

    def generate_html(
        self,
        plan: TestFlowPlan,
        items: List[FlowReportItem],
        preview_url: str = "",
        audit: Optional[SuperhumanAuditReport] = None,
    ) -> str:
        from jinja2 import Template

        passed_count = sum(1 for i in items if i.passed)
        failed_count = sum(1 for i in items if not i.passed)
        specs_count = sum(1 for i in items if i.spec_code_ts)
        quality_score = audit.quality_score if audit else (100 if failed_count == 0 else max(20, 100 - failed_count * 25))

        # Audit messages and verdicts carry text from the tested site and the model: escape it.
        screenshots = {}
        for item in items:
            shot = item.verdict.screenshot_evidence
            if not item.passed and shot and Path(shot).is_file() and Path(shot).stat().st_size <= MAX_EMBEDDED_PNG:
                screenshots[item.flow_id] = base64.b64encode(Path(shot).read_bytes()).decode("ascii")

        tmpl = Template(DASHBOARD_TEMPLATE, autoescape=True)
        return tmpl.render(
            screenshots=screenshots,
            reproduction_note=reproduction_note,
            automatic_checks=AUTOMATIC_CHECKS,
            preview_url=preview_url,
            plan=plan,
            items=items,
            passed_count=passed_count,
            failed_count=failed_count,
            specs_count=specs_count,
            total_flows=len(items),
            quality_score=quality_score,
            audit=audit,
        )

    def save_dashboard(self, html_content: str, output_path: Path | str) -> Path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_content, encoding="utf-8")
        return path


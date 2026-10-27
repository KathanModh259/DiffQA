# DiffQA 🚀

> **Autonomous QA Agent that tests web apps like an expert human tester after every PR code change.**  
> Built for the **Meta Global AI Developer Hackathon 2026** (Indie Developer Track).

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.10+](https://img.shields.io/badge/Python-3.10+-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-Automated_Browser-45ba4b.svg?logo=playwright&logoColor=white)](https://playwright.dev/)
[![Powered by: Meta Model API](https://img.shields.io/badge/Powered%20by-Meta%20Model%20API-0081FB.svg?logo=meta&logoColor=white)](https://about.meta.com/)

---

## 🎯 The Problem

When shipping client web apps and PRs:
- **Every pull request requires manual click-through testing**: Engineers or QA spend hours clicking through flows.
- **Regressions slip through**: Traditional scripted E2E tests only cover what someone explicitly remembered to write. If a small CSS tweak breaks a mobile modal or an edge-case form submit throws a silent 500 error, CI says "green" while production breaks.

## 💡 What DiffQA Does

**DiffQA bridges the gap between code diffs and live browser testing:**

```
  Git PR Diff  ───┐
                  ├─► [Diff Analyzer] ─► [Flow Planner] ─► [Autonomous Playwright Agent]
  Preview URL  ───┘                                              │
                                                                 ▼
                                                    [Multimodal Bug Oracle]
                                                                 │
                                    ┌────────────────────────────┴───────────────────────────┐
                                    ▼                                                        ▼
                        [Rich PR Markdown Report]                              [Synthesized Playwright Test]
                     (Pass/Fail, Traces, Screenshots)                        (repro-bug-*.spec.ts for CI)
```

1. **Reads the PR Diff**: Uses Meta models (`Muse Code` / `Llama 3.3`) to analyze changed files, routes, components, and state schemas.
2. **Maps User Flows & Edge Cases**: Synthesizes affected routes, primary happy paths, and risk-targeted exploratory paths.
3. **Drives Playwright in Headless Browser**: Navigates the preview URL, interacts with elements, tests flows and edge cases.
4. **Multimodal Regression Oracle**: Evaluates DOM state, console logs, network errors (4xx/5xx), visual screenshots, and business invariants.
5. **Generates Reproducible Playwright Tests**: When a bug is caught, DiffQA writes an executable `@playwright/test` script reproducing the exact failure so the bug can never regress.
6. **Posts PR Comment**: Summarizes results with pass/fail badges, repro steps, screenshots, and the committed test spec.

---

## 📁 Repository Layout

```tree
DiffQA/
├── diffqa/
│   ├── __init__.py
│   ├── cli.py               # Typer CLI commands (run, scan, eval, report)
│   ├── config.py            # Environment & model configuration
│   ├── analyzer/            # PR diff parsing & route/flow mapping
│   │   ├── diff_parser.py   # Unified diff parser
│   │   └── flow_planner.py  # LLM-driven user flow & edge case planner
│   ├── agent/               # Autonomous Playwright explorer
│   │   ├── browser.py       # Playwright browser lifecycle & page controller
│   │   ├── dom_observer.py  # Interactive element tree & Set-of-Marks tagging
│   │   ├── actions.py       # Action schema & execution (click, fill, navigate)
│   │   └── runner.py        # Autonomous agent loop
│   ├── judge/               # Regression oracles
│   │   ├── functional.py    # Console/network/DOM failure detector
│   │   ├── visual.py        # Visual layout & screenshot inspection
│   │   └── oracle.py        # Composite verdict judge
│   ├── synthesizer/         # Playwright test generator
│   │   └── test_writer.py   # Generates TypeScript / Python Playwright specs
│   └── reporter/            # PR Markdown & CI output
│       ├── markdown.py      # Rich PR comment formatter
│       └── artifacts.py     # Screenshot & trace bundling
├── benchmarks/              # Seeded bug evaluation testbed
│   ├── testbed_app/         # Open-source target web app
│   └── scenarios/           # Seeded PR diffs & expected bug catalog
├── tests/                   # DiffQA unit & integration tests
├── .github/workflows/       # GitHub Action workflow
├── pyproject.toml
└── README.md
```

---

## 🚀 Quickstart

### Prerequisites
- Python 3.10+
- Node.js 18+ (for running Playwright browser dependencies)

### Setup
```bash
# Clone the repository
git clone https://github.com/KathanModh259/DiffQA.git
cd DiffQA

# Create and activate virtual environment
python -m venv venv
source venv/bin/activate  # Or on Windows: .\venv\Scripts\activate

# Install dependencies & Playwright browsers
pip install -e .
playwright install chromium
```

### Configure Models
Create a `.env` file with your Meta Model API credentials or inference partner keys:
```env
META_API_KEY=your_key_here
# Or partner endpoints (Baseten, Modal, Fireworks, Ollama)
MODEL_PROVIDER=meta   # Options: meta, baseten, fireworks, modal, ollama, mock
MODEL_NAME=meta/muse-code
```

### Run DiffQA on a PR & Preview URL
```bash
# Run DiffQA against a PR diff and a local or preview URL
diffqa run --diff ./sample-pr.diff --url http://localhost:3000 --output ./diffqa-report
```

---

## 📊 Evaluation & Benchmark
DiffQA follows an **eval-first** design. Run the built-in benchmark to test bug detection accuracy:
```bash
diffqa eval --benchmark ./benchmarks/scenarios
```

---

## 🏆 Hackathon Submission Details
- **Participant:** Kathan Modh ([LinkedIn](https://www.linkedin.com/in/kathanmodh/) | [GitHub](https://github.com/KathanModh259))
- **Track:** Indie Developer Track
- **Event:** Meta Global AI Developer Hackathon (Oct 26 – Nov 4, 2026)


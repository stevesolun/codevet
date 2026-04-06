# codevet

**AI Code Vet / Auto-Fixer** — Catch the bugs AI coding tools miss.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests: 45 passing](https://img.shields.io/badge/tests-45%20passing-brightgreen.svg)]()
[![Privacy: 100% Local](https://img.shields.io/badge/privacy-100%25%20local-purple.svg)]()

---

AI tools like Claude Code, Cursor, and Copilot generate code that's *almost* right — the worst kind of wrong. Subtle bugs, missed edge cases, security holes, and logic drifts that waste more time debugging than writing fresh.

**codevet** fixes this. One command: vet any AI-generated file, auto-generate targeted tests, auto-fix with confidence scoring. Everything runs locally — your code never leaves your machine.

## How It Works

```
codevet fix app.py
```

1. **Sandbox** — Your code runs in a locked-down Docker container (read-only, no network, 30s timeout)
2. **Test** — Ollama generates 8-12 targeted pytest cases (edge mutations, security, performance)
3. **Fix** — Failed tests trigger up to 3 auto-fix iterations with AST-validated patches
4. **Score** — Confidence scored 0-100: `(test_pass_rate × 0.7) + (LLM_self_critique × 0.3)`

```
CodeVet Results for app.py
Model: gemma2:9b | Duration: 12.3s

Tests: 10 passed, 2 failed, 0 errors (12 total)
Fix: Success in 2 iteration(s)

┌─ Confidence ─────────────────────────┐
│  B  82/100  (Good confidence)        │
└──────────────────────────────────────┘

┌─ Diff ───────────────────────────────┐
│ - def authenticate(user, pw):        │
│ -     query = f"SELECT * FROM ..."   │
│ + def authenticate(user, pw):        │
│ +     query = "SELECT * FROM ..."    │
│ +     cursor.execute(query, (user,)) │
└──────────────────────────────────────┘
```

## Install

```bash
pip install codevet
```

**Requirements:** Python 3.11+, Docker, [Ollama](https://ollama.ai) with any model (default: `gemma2:9b`)

## Usage

```bash
# Vet and fix a file
codevet fix app.py

# Use a different model
codevet fix app.py --model qwen2.5-coder:7b

# JSON output (for CI/CD or tooling)
codevet fix app.py --json

# Vet a PR diff
codevet fix --diff pr.diff

# Pipe from stdin
cat suspicious_code.py | codevet fix
```

### CLI Options

| Flag | Description | Default |
|------|-------------|---------|
| `--model`, `-m` | Ollama model | `gemma2:9b` |
| `--json`, `-j` | JSON output | `false` |
| `--diff`, `-d` | PR diff file | — |
| `--timeout`, `-t` | Sandbox timeout (seconds) | `30` |
| `--max-iterations` | Fix loop cap | `3` |
| `--image` | Docker image | `python:3.11-slim` |

## VSCode Extension

The extension adds a "CodeVet: Vet & Fix Current File" command to the right-click menu for Python files. Results appear in a side panel with:
- Color-coded confidence grade (A-F)
- Before/after diff
- Test results summary

Install from `vscode-extension/` directory.

## Architecture

```
codevet fix app.py
       │
       ▼
┌─────────────┐    ┌──────────────┐    ┌─────────────┐
│  sandbox.py │───▶│  vetter.py   │───▶│  fixer.py   │
│  Docker     │    │  Ollama test │    │  3-iter loop │
│  isolation  │    │  generation  │    │  difflib+AST │
└─────────────┘    └──────────────┘    └─────────────┘
                                              │
                                              ▼
                                       ┌─────────────┐
                                       │  scorer.py  │
                                       │  0-100      │
                                       │  confidence  │
                                       └─────────────┘
```

**8 modules:** `cli` · `sandbox` · `vetter` · `fixer` · `scorer` · `models` · `prompts` · `utils`

## Security Model

The Docker sandbox is the security boundary:
- `--read-only` filesystem
- `--network none` (no internet access)
- `--tmpfs /tmp:size=100m` (ephemeral scratch space)
- `--security-opt seccomp=unconfined` (broad syscall compat)
- Project directory mounted **read-only**
- Hard 30-second timeout with forced container kill
- Memory limited to 256MB, max 100 PIDs

Your code is analyzed locally. Nothing is uploaded. Ollama runs on your machine.

## Development

```bash
# Clone and setup
git clone https://github.com/stevesolun/codevet.git
cd codevet
uv sync --dev

# Run tests (45 tests: 32 unit, 9 integration, 5 e2e)
uv run pytest

# Lint
uv run ruff check .

# Type check
uv run mypy codevet/ --ignore-missing-imports
```

## Why Not Just Use a Linter?

Linters catch syntax and style. Codevet catches **logic bugs** — the kind AI tools introduce:
- Off-by-one errors that pass type checks
- Missing input validation that linters don't flag
- SQL injection from f-string queries
- Edge cases on empty inputs, None values, boundary conditions
- Type coercion bugs (`str(a) + str(b)` instead of `a + b`)

Codevet doesn't replace your linter. It catches what your linter can't.

## License

MIT

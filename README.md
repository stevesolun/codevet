# codevet

**AI Code Vet / Auto-Fixer** — Catch the bugs AI coding tools miss.

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests: 230 passing](https://img.shields.io/badge/tests-230%20passing-brightgreen.svg)]()
[![Privacy: 100% Local](https://img.shields.io/badge/privacy-100%25%20local-purple.svg)]()

---

AI tools like Claude Code, Cursor, and Copilot generate code that's *almost* right — the worst kind of wrong. Subtle bugs, missed edge cases, security holes, and logic drifts that waste more time debugging than writing fresh.

**codevet** fixes this. One command vets any AI-generated file: it auto-generates targeted tests, runs them in a hardened Docker sandbox, auto-fixes failures with up to 3 LLM iterations, and gives you a confidence score. Everything runs locally — your code never leaves your machine.

## Table of Contents

- [What you get](#what-you-get)
- [Install for dummies (step by step)](#install-for-dummies-step-by-step)
- [First run](#first-run)
- [Configuration (`codevet.yaml`)](#configuration-codevetyaml)
- [Picking the right model](#picking-the-right-model)
- [CLI reference](#cli-reference)
- [How it works](#how-it-works)
- [Security model](#security-model)
- [Credits](#credits)
- [Development](#development)
- [License](#license)

---

## What you get

```
$ codevet fix app.py

Vetting app.py...
Found issues: 6 failed, 0 errors. Auto-fixing...
Iteration 1: 4/6 passing
Iteration 2: 6/6 passing — fix succeeded.

CodeVet Results for app.py
Model: qwen2.5-coder:14b | Duration: 47.3s

Tests: 6 passed, 0 failed (6 total)
Fix: Success in 2 iteration(s)

┌─ Confidence ─────────────────────────┐
│  A  92/100  (Excellent confidence)   │
└──────────────────────────────────────┘

┌─ Diff ───────────────────────────────┐
│ - def authenticate(user, pw):        │
│ -     query = f"SELECT * FROM ..."   │
│ + def authenticate(user, pw):        │
│ +     query = "SELECT * FROM ..."    │
│ +     cursor.execute(query, (user,)) │
└──────────────────────────────────────┘
```

---

## Install for dummies (step by step)

Codevet has three external dependencies: Python, Docker, and Ollama. The steps below assume you have **none** of them installed. If you already have one, just skip that section.

### 1. Install Python 3.11+

| Platform | Instructions |
|---|---|
| **Windows** | Download from [python.org](https://www.python.org/downloads/windows/). During install, **tick "Add python.exe to PATH"**. |
| **macOS** | `brew install python@3.11` (install Homebrew from [brew.sh](https://brew.sh) first if needed). |
| **Linux** | `sudo apt install python3.11 python3.11-venv` (Debian/Ubuntu) or `sudo dnf install python3.11` (Fedora). |

Verify:

```bash
python --version          # should print 3.11.x or later
```

### 2. Install Docker

Codevet runs all generated tests inside a **Docker container** so they cannot touch your real machine. You need Docker installed and running.

| Platform | Instructions |
|---|---|
| **Windows** | Install [Docker Desktop](https://www.docker.com/products/docker-desktop/). Launch it once and wait for the whale icon to turn solid in the system tray. |
| **macOS** | Install [Docker Desktop](https://www.docker.com/products/docker-desktop/), or `brew install --cask docker` then launch the app. |
| **Linux** | Follow the official guide for your distro: [docs.docker.com/engine/install](https://docs.docker.com/engine/install/). Add yourself to the `docker` group so you don't need `sudo`: `sudo usermod -aG docker $USER` then log out and back in. |

Verify:

```bash
docker version            # should print Server: Docker Engine ...
docker ps                 # should NOT print 'Cannot connect'
```

### 3. Install Ollama and pull a model

Ollama runs the LLM locally. Codevet talks to it over `http://127.0.0.1:11434`.

| Platform | Instructions |
|---|---|
| **Windows / macOS** | Download the installer from [ollama.com/download](https://ollama.com/download) and run it. Ollama starts as a background service automatically. |
| **Linux** | `curl -fsSL https://ollama.com/install.sh \| sh` |

Pull a model. The default is **qwen2.5-coder:7b** (4.7 GB, needs ~8 GB free RAM):

```bash
ollama pull qwen2.5-coder:7b
```

Verify:

```bash
ollama list               # should list qwen2.5-coder:7b
```

> **Don't know which model to pull?** See [Picking the right model](#picking-the-right-model) below. The short answer: 7b for 16 GB laptops, 14b for desktops.

### 4. Install codevet

We recommend [`uv`](https://github.com/astral-sh/uv), which is faster than pip and handles the virtualenv for you. If you prefer pip, the same commands work without `uv run`.

```bash
# Clone the repo
git clone https://github.com/stevesolun/codevet.git
cd codevet

# Option A: with uv (recommended)
uv sync                                # installs codevet + deps in .venv
uv run codevet --help                  # verify it works

# Option B: with pip
python -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e .
codevet --help
```

That's it. Codevet is installed.

---

## First run

Create a buggy Python file to test against:

```python
# buggy.py
def authenticate(username, password):
    query = f"SELECT * FROM users WHERE name='{username}' AND pass='{password}'"
    return query

def get_element(lst, index):
    return lst[index + 1]

def calculate_average(numbers):
    return sum(numbers) / len(numbers)
```

Run codevet:

```bash
uv run codevet fix buggy.py
```

What happens:

1. **Preflight check** — codevet downloads `llmfit` (see [Credits](#credits)) and verifies your hardware can run the configured model. If the model is too large for your RAM, it will refuse to load.
2. **Test generation** — Ollama generates 6-12 targeted pytest cases (SQL injection probes, edge cases, performance traps).
3. **Sandbox execution** — Tests run inside the Docker sandbox. Codevet reports how many failed.
4. **Auto-fix loop** — If any tests failed, codevet asks the LLM for a fix and re-runs the tests. Up to 3 iterations.
5. **Confidence score** — A weighted blend of test pass-rate and LLM self-critique, graded A through F.

> **First run is slow.** The Docker sandbox image (`codevet-sandbox:0.1.0`) is built locally on first invocation. Subsequent runs reuse the cached image.

---

## Configuration (`codevet.yaml`)

Codevet ships with a fully commented example config at the repo root: **[`codevet.yaml.example`](./codevet.yaml.example)**. Copy it next to your project to customize:

```bash
cp codevet.yaml.example codevet.yaml
```

…or generate a fresh copy anywhere with:

```bash
uv run codevet init-config              # writes ./codevet.yaml
uv run codevet init-config --user       # writes ~/.config/codevet/config.yaml
```

### Where codevet looks for config

First match wins:

| Priority | Location | When to use |
|---|---|---|
| 1 | `--config <path>` CLI flag | One-off override |
| 2 | `./codevet.yaml` | Project-specific settings |
| 3 | `~/.config/codevet/config.yaml` | User-global default |
| 4 | Built-in defaults | No config file at all |

### What you can configure

Every tunable lives in [`codevet.yaml.example`](./codevet.yaml.example). Highlights:

| Setting | Default | What it does |
|---|---|---|
| `model` | `qwen2.5-coder:7b` | Ollama model for test gen + auto-fix |
| `image` | `python:3.11-slim` | Base Docker image for the sandbox |
| `timeout_seconds` | `120` | Per-sandbox-run hard timeout (clamped to [5, 1800]) |
| `max_iterations` | `3` | Auto-fix loop cap (hard-capped at 3) |
| `mem_limit` | `256m` | Container memory cap |
| `pids_limit` | `64` | Max processes inside the container |
| `confidence_pass_weight` | `0.7` | Weight for the test pass-rate component of the confidence score |
| `confidence_critique_weight` | `0.3` | Weight for the LLM self-critique component (must sum to 1.0 with the above) |

CLI flags always override config file values.

---

## Picking the right model

The model is the single biggest factor in fix quality. Codevet runs a **mandatory hardware-fit preflight** before loading any model — if it won't fit your RAM, you'll get a clear error before anything tries to load.

Recommended models, by hardware tier:

| Hardware | Model | Size | Fix quality | Notes |
|---|---|---|---|---|
| 8 GB RAM, no GPU | `qwen2.5-coder:1.5b` | 1.0 GB | ⭐⭐ | Fast, weak on security bugs |
| **16 GB RAM, no GPU** | **`qwen2.5-coder:7b`** | **4.7 GB** | **⭐⭐⭐** | **Default — balanced** |
| 32 GB RAM, any GPU | `qwen2.5-coder:14b` | 9.0 GB | ⭐⭐⭐⭐ | Best fix quality for most users |
| 64 GB RAM + 16 GB+ GPU | `qwen2.5-coder:32b` | 19 GB | ⭐⭐⭐⭐⭐ | Maximum quality, slow on CPU |

Check any model against your hardware first:

```bash
uv run codevet preflight qwen2.5-coder:14b
```

### CPU vs GPU timing (real numbers)

On a 16 GB laptop with no GPU acceleration:

| Phase | qwen2.5-coder:7b | qwen2.5-coder:14b |
|---|---|---|
| Test generation (single Ollama call) | ~4 min | ~8 min |
| Each fix iteration | ~5-7 min | ~10-15 min |
| Sandbox execution | <2 sec | <2 sec |
| **Full pipeline** (gen + 3 iters + critique) | **~25 min** | **~45 min** |

If you have a GPU with sufficient VRAM, divide these numbers by 5-10x.

---

## CLI reference

```bash
codevet fix <file.py> [OPTIONS]
codevet init-config [OPTIONS]
codevet preflight <model>
```

### `codevet fix`

| Flag | Description | Default |
|------|-------------|---------|
| `--config`, `-c` | Path to a `codevet.yaml` file (overrides discovery) | — |
| `--model`, `-m` | Override the configured Ollama model | from config |
| `--timeout`, `-t` | Override the sandbox timeout in seconds | from config |
| `--max-iterations` | Override the fix-loop cap | from config |
| `--image` | Override the Docker base image | from config |
| `--json`, `-j` | Emit machine-readable JSON instead of pretty output | `false` |
| `--diff`, `-d` | Read a unified diff from a file instead of a Python file | — |
| `--verbose`, `-v` | Show INFO-level phase logging | `false` |
| `--skip-preflight` | Skip the llmfit hardware-fit check (not recommended) | `false` |

### `codevet init-config`

| Flag | Description | Default |
|------|-------------|---------|
| `--user` | Write to `~/.config/codevet/config.yaml` instead of `./codevet.yaml` | `false` |
| `--force`, `-f` | Overwrite an existing file | `false` |

### `codevet preflight`

Verify a model will fit your hardware before configuring it. Powered by `llmfit`. See [Credits](#credits).

```bash
codevet preflight qwen2.5-coder:14b
```

---

## How it works

```
codevet fix app.py
       │
       ▼
┌────────────┐  ┌──────────┐  ┌────────────┐  ┌──────────┐  ┌─────────┐
│ preflight  │─▶│ vetter   │─▶│ sandbox    │─▶│ fixer    │─▶│ scorer  │
│ llmfit     │  │ Ollama   │  │ Docker     │  │ 3-iter   │  │ 0-100   │
│ hw check   │  │ test gen │  │ isolation  │  │ AST guard│  │ + grade │
└────────────┘  └──────────┘  └────────────┘  └──────────┘  └─────────┘
```

**8 modules:** `cli` · `config` · `preflight` · `vetter` · `sandbox` · `fixer` · `scorer` · `models` · `prompts` · `utils`

The auto-fix loop validates every LLM patch against Python's `ast` module before executing it — invalid syntax (e.g. markdown-fenced output) is caught and the iteration is skipped instead of crashing the run.

---

## Security model

The Docker sandbox is the security boundary. Every container runs with:

- `--read-only` filesystem
- `--network none` — no internet access at all
- `--tmpfs /tmp:size=100m` — ephemeral 100 MB scratch space
- `--security-opt no-new-privileges` — drops the ability to escalate
- `--cap-drop=ALL` — all Linux capabilities removed
- `--user 1001` — non-root execution
- `--memory 256m` — memory cap
- `--pids-limit 64` — process count cap
- Hard wall-clock timeout with forced container kill

Your project directory is mounted **read-only**. Generated tests cannot modify your source files.

Your code is analyzed locally. Nothing is uploaded. Ollama runs on your machine. The only network call codevet ever makes is the one-time download of the `llmfit` binary from its upstream GitHub release (cached locally afterwards).

---

## Credits

### llmfit

Codevet's preflight hardware-fit check is **powered by [`llmfit`](https://github.com/AlexsJones/llmfit)**, an MIT-licensed Rust CLI by **[Alex Jones](https://github.com/AlexsJones)** that detects host hardware (RAM, CPU, GPU) and scores LLM models against it.

- **Repo:** https://github.com/AlexsJones/llmfit
- **Author:** Alex Jones ([@AlexsJones](https://github.com/AlexsJones))
- **License:** MIT

Codevet downloads the official prebuilt llmfit binary from upstream releases on first use and caches it locally. The binary is **never redistributed** in this repository — every install pulls it fresh and stays automatically up-to-date with the latest version (24-hour cache on the version lookup).

Without llmfit, codevet would have no good way to tell users in advance whether their chosen model will actually run on their machine. **Huge thanks to Alex** for building llmfit and making it MIT-licensed.

If you find llmfit useful, please **[star the upstream repo](https://github.com/AlexsJones/llmfit)**.

For full third-party credits, see [`NOTICE.md`](./NOTICE.md).

---

## Development

```bash
# Clone and setup
git clone https://github.com/stevesolun/codevet.git
cd codevet
uv sync --dev

# Run the test suite (230 tests)
uv run pytest -q

# Type check
uv run mypy codevet/ --strict

# Lint
uv run ruff check .
```

Test coverage:

| Suite | Count |
|---|---|
| Unit tests | 195 |
| Integration tests | 25 |
| End-to-end (real Docker + Ollama) | 10 |
| **Total** | **230** |

---

## License

MIT — see [`LICENSE`](./LICENSE).

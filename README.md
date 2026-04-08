# codevet

**AI Code Vet / Auto-Fixer** — Catch the bugs AI coding tools miss.

[![PyPI version](https://img.shields.io/pypi/v/codevet.svg)](https://pypi.org/project/codevet/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Tests: 230 passing](https://img.shields.io/badge/tests-230%20passing-brightgreen.svg)]()
[![Privacy: 100% Local](https://img.shields.io/badge/privacy-100%25%20local-purple.svg)]()

---

AI tools like Claude Code, Cursor, and Copilot generate code that's *almost* right — the worst kind of wrong. Subtle bugs, missed edge cases, security holes, and logic drifts that waste more time debugging than writing fresh.

**codevet** fixes this. One command vets any AI-generated file: it auto-generates targeted tests, runs them in a hardened Docker sandbox, auto-fixes failures with up to 3 LLM iterations, and gives you a confidence score. Everything runs locally — your code never leaves your machine.

## Table of Contents

- [What you get](#what-you-get)
- [Install](#install)
- [Use cases](#use-cases)
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

## Install

### Prerequisites

You need three things running before codevet will work:

| Dependency | What it does | Install |
|---|---|---|
| **Python 3.11+** | Runs codevet itself | [python.org](https://www.python.org/downloads/) · `brew install python@3.11` · `apt install python3.11` |
| **Docker** | Isolated sandbox for generated tests | [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/) · `brew install --cask docker` |
| **Ollama** | Local LLM for test generation and auto-fix | [ollama.com/download](https://ollama.com/download) · `curl -fsSL https://ollama.com/install.sh \| sh` |

Verify all three are running:

```bash
python --version         # 3.11.x or later
docker ps                # should not print 'Cannot connect'
ollama list              # should list at least one model
```

### Install codevet

```bash
pip install codevet
```

Or with [`uv`](https://github.com/astral-sh/uv) (faster, manages its own virtualenv):

```bash
uv tool install codevet
```

Verify:

```bash
codevet --help
```

### Pull a model

The default model is **qwen2.5-coder:7b** (4.7 GB, needs ~8 GB free RAM):

```bash
ollama pull qwen2.5-coder:7b
```

> **Not sure which model to pick?** See [Picking the right model](#picking-the-right-model) below.

---

## Use cases

### 1. Vet a file from Claude Code or Cursor

You asked an AI to write a new module. It looks plausible. Run it through codevet before you commit:

```bash
codevet fix path/to/ai_generated.py
```

Codevet generates 6-12 targeted pytest cases — edge cases, type coercion traps, SQL injection probes — runs them in a Docker sandbox, and auto-fixes anything that fails. You get a confidence score and a clean diff to review.

### 2. Review a PR diff before merging

AI-assisted PRs often pass CI but fail on edge cases that the tests don't cover. Pipe the diff through codevet:

```bash
gh pr diff 42 > pr42.diff
codevet fix --diff pr42.diff
```

Codevet analyzes the changed code in the diff, generates tests for it specifically, and reports on what it found.

### 3. Spot-check a specific function

Got a suspicious function you want to probe without running the whole file?

```bash
# Extract the function to a temp file and vet it
codevet fix utils/auth.py --model qwen2.5-coder:14b
```

Use a larger model (`14b`, `32b`) when the code involves security, cryptography, or complex business logic.

### 4. Machine-readable output for scripts and CI

```bash
codevet fix app.py --json | jq '.score'
# 92

codevet fix app.py --json | jq '.fix_succeeded'
# true
```

Use `--json` to integrate codevet into pre-commit hooks, CI pipelines, or your own tooling.

### 5. Hardware-fit check before switching models

Before changing your `codevet.yaml` to use a larger model:

```bash
codevet preflight qwen2.5-coder:32b
# Will tell you if your machine has enough RAM before you commit to it
```

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
codevet fix buggy.py
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

Generate a config file for your project:

```bash
codevet init-config                                              # writes ./codevet.yaml
codevet init-config --path ~/.config/codevet/config.yaml        # writes user-global config
```

The generated file is fully commented. A reference copy is also at [`codevet.yaml.example`](./codevet.yaml.example) in this repo.

### Where codevet looks for config

First match wins:

| Priority | Location | When to use |
|---|---|---|
| 1 | `--config <path>` CLI flag | One-off override |
| 2 | `./codevet.yaml` | Project-specific settings |
| 3 | `~/.config/codevet/config.yaml` | User-global default |
| 4 | Built-in defaults | No config file at all |

### Key settings

| Setting | Default | What it does |
|---|---|---|
| `model` | `qwen2.5-coder:7b` | Ollama model for test gen + auto-fix |
| `image` | `python:3.11-slim` | Base Docker image for the sandbox |
| `timeout_seconds` | `120` | Per-sandbox-run hard timeout (clamped to [5, 1800]) |
| `max_iterations` | `3` | Auto-fix loop cap (hard-capped at 3) |
| `mem_limit` | `256m` | Container memory cap |
| `pids_limit` | `64` | Max processes inside the container |
| `confidence_pass_weight` | `0.7` | Weight for the test pass-rate component of the confidence score |
| `confidence_critique_weight` | `0.3` | Weight for the LLM self-critique component (must sum to 1.0) |

CLI flags always override config file values.

---

## Picking the right model

The model is the single biggest factor in fix quality. Codevet runs a **mandatory hardware-fit preflight** before loading any model — if it won't fit your RAM, you'll get a clear error before anything tries to load.

Recommended models by hardware tier:

| Hardware | Model | Size | Fix quality | Notes |
|---|---|---|---|---|
| 8 GB RAM, no GPU | `qwen2.5-coder:1.5b` | 1.0 GB | ⭐⭐ | Fast, weak on security bugs |
| **16 GB RAM, no GPU** | **`qwen2.5-coder:7b`** | **4.7 GB** | **⭐⭐⭐** | **Default — balanced** |
| 32 GB RAM, any GPU | `qwen2.5-coder:14b` | 9.0 GB | ⭐⭐⭐⭐ | Best fix quality for most users |
| 64 GB RAM + 16 GB+ GPU | `qwen2.5-coder:32b` | 19 GB | ⭐⭐⭐⭐⭐ | Maximum quality, slow on CPU |

Check any model against your hardware first:

```bash
codevet preflight qwen2.5-coder:14b
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
codevet fix --diff <pr.diff> [OPTIONS]
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
| `--verbose`, `-v` | Show INFO-level phase logging (includes llmfit SHA-256) | `false` |
| `--skip-preflight` | Skip the llmfit hardware-fit check | `false` |

### `codevet init-config`

| Flag | Description | Default |
|------|-------------|---------|
| `--path`, `-p` | Where to write the config file | `./codevet.yaml` |
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

**10 modules:** `cli` · `config` · `preflight` · `vetter` · `sandbox` · `fixer` · `scorer` · `models` · `prompts` · `utils`

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

### llmfit binary trust model

On first run, codevet downloads the `llmfit` prebuilt binary from **[AlexsJones/llmfit releases](https://github.com/AlexsJones/llmfit/releases)** — the official upstream repository. The binary is MIT-licensed and is never redistributed in this repo.

**What codevet does to help you verify it:**

- The SHA-256 of the downloaded archive is computed and logged at INFO level:
  ```
  INFO  llmfit archive SHA-256: <hex digest>
  INFO  Verify at: https://github.com/AlexsJones/llmfit/releases (download checksum for <asset>)
  ```
- Run with `--verbose` to see these lines in your terminal.
- You can manually compare the logged digest against the release page on GitHub.

**To skip the download entirely**, use `--skip-preflight`:

```bash
codevet fix app.py --skip-preflight
```

The check (and binary download) is skipped completely. You lose the hardware-fit warning, but nothing else changes.

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
uv sync --extra dev

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

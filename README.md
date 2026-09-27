# AgentOS — Autonomous AI Agent Platform

AgentOS takes a single high-level goal — *"Build a PyQt6 calculator
application"* — and turns it into a planned, generated, verified, tested,
and self-repaired software project, using a team of specialized AI worker
agents coordinated by a central orchestrator, observable live from a
desktop dashboard.

---

## Table of contents

1. [What's built](#whats-built)
2. [How a run actually flows, step by step](#how-a-run-actually-flows-step-by-step)
3. [Full file structure, with comments](#full-file-structure-with-comments)
4. [Setup](#setup)
5. [Running it — dashboard (recommended)](#running-it--dashboard-recommended)
6. [Running it — command line](#running-it--command-line)
7. [Configuring LLM providers](#configuring-llm-providers)
8. [The agents, and what each is restricted to](#the-agents-and-what-each-is-restricted-to)
9. [Safety mechanisms already in place](#safety-mechanisms-already-in-place)
10. [What is NOT built yet](#what-is-not-built-yet)
11. [Troubleshooting](#troubleshooting)

---

## What's built

- **Multi-provider LLM layer** — Anthropic, OpenAI, Gemini, OpenRouter,
  Ollama (local/offline), LM Studio (local/offline), and a deterministic
  `mock` provider for zero-setup runs. The **planner** role and the
  **worker** role can each use a *different* provider/model — e.g. a
  strong cloud model to plan, a fast local model to write code.
- **Planner** — sends the goal to the planner LLM and gets back a
  structured plan tree (`phases → milestones → tasks → subtasks →
  atomic_actions`), validated before anything is executed.
- **Specialized worker agents** — a Coding Agent (with role variants for
  backend/api/database/ui/refactoring work), a Testing Agent, a
  Documentation Agent, a DevOps Agent, and a Security Agent — each with
  its own prompt and its own **enforced, restricted** set of tool
  permissions (a Documentation Agent cannot call `run_pytest`; this is a
  runtime `PermissionError`, not just a convention).
- **Multi-Agent Coordinator** — routes every atomic action to the correct
  specialist based on an `agent` tag the Planner attaches to it.
- **Verification Engine** — real `py_compile` syntax checking on every
  generated `.py` file.
- **Testing Engine** — actually runs the generated project's `pytest`
  suite.
- **Repair Engine** — on any verification or test failure, sends the
  broken file and the exact error back to the worker LLM, rewrites the
  file with the fix, and re-checks — up to `max_repair_attempts` times,
  then escalates instead of looping forever or silently reporting success.
- **Security Agent** — runs a real deterministic pattern scan (regex, not
  an LLM opinion) for `eval()`, `exec()`, `os.system()`, `shell=True`,
  unsafe unpickling, and unparameterized SQL, over every generated file,
  and only calls the LLM to fix what it actually found.
- **Memory Manager** — SQLite-backed project/task/event history, queryable
  after the fact.
- **Live desktop dashboard** — a PyQt6 GUI wired directly to the real
  engine above (not a mock-up), running it on a background thread so the
  UI never freezes, and streaming every real event live: a Goal bar, a
  Model Manager panel, an Agent Dashboard with live per-agent status, a
  Task Queue table, a scrolling Execution Log, a real file-system Project
  Tree, and a Memory/History table.

---

## How a run actually flows, step by step

This is the exact sequence that happens whether you use the CLI or the
dashboard — the dashboard just visualizes it live.

1. **You provide a goal.** E.g. *"Build a PyQt6 markdown note-taking app."*
2. **`CoreBrain.run()`** (`agentos/brain.py`) is called with that goal and
   a project name. It is the single orchestrator for everything below.
3. **Planner** (`agentos/planner.py`) sends the goal to the *planner* LLM
   with a system prompt instructing it to return a JSON plan tree:
   `phases → milestones → tasks → subtasks → atomic_actions`. Each
   atomic action is small, typed, and tagged with which agent should
   handle it (`coding`, `testing`, `documentation`, `devops`, `security`,
   or a Coding Agent specialization like `backend`/`api`/`database`/`ui`/
   `refactoring`).
4. **Validation.** The Planner rejects a malformed plan (missing fields,
   empty action lists) before anything is executed — a bad plan fails
   fast instead of quietly producing broken atomic actions downstream.
5. **The plan is saved** to the Memory Manager (SQLite) as a new project
   record, and the tree is **flattened** into an ordered list of atomic
   actions (`flatten_atomic_actions`).
6. **For each atomic action, the Multi-Agent Coordinator** (`agentos/agents.py`)
   looks at its `agent` tag and dispatches it to that specialist:
   - `create_folder` → handled directly by the Tool Manager (no LLM call
     needed for a mkdir).
   - `generate_file` → **Coding Agent** (or a role-specialized prompt
     variant) asks the *worker* LLM to write the complete file content.
   - `generate_test` → **Testing Agent** asks the worker LLM to write a
     pytest file.
   - `generate_doc` → **Documentation Agent** writes docs/README content.
   - `generate_config` → **DevOps Agent** writes build/config files.
   Every write goes through the **Tool Manager** (`agentos/tools.py`),
   which resolves the path and *rejects it* if it would escape the
   project's workspace directory.
7. **Verification Engine** (`agentos/verification.py`) runs `py_compile`
   on every generated `.py` file. This is a real syntax check, not an
   LLM asked "does this look right?".
8. **If verification fails**, the **Repair Engine** (`agentos/repair.py`)
   sends the broken file's exact content and exact error back to the
   worker LLM, gets a corrected file, rewrites it, and re-verifies — up
   to `max_repair_attempts` times (default 3). If it still fails after
   that, the action is marked `failed_unrepaired` and the whole run is
   flagged `escalation_required` instead of silently reporting success.
9. **Security Agent scan.** Every generated `.py` file is scanned for
   dangerous patterns: `eval()`, `exec()`, `os.system()`,
   `subprocess(..., shell=True)`, unpickling untrusted data, and
   unparameterized SQL string-formatting. If nothing is found, it's
   marked `clean`. If something is found, the Security Agent asks the LLM
   to rewrite the file to remove it, then **re-scans** — reporting
   `flagged_and_fixed` only if the re-scan actually comes back clean, or
   `flagged_unresolved` if it doesn't (it never just claims success).
10. **Once every action is executed**, the **Testing Engine**
    (`agentos/testing.py`) runs the full `pytest` suite for real.
11. **If tests fail**, the Repair Engine is invoked again on the test
    file(s), and the suite is re-run after each fix attempt.
12. **Final status** is set to `completed` (all verifications passed, no
    unrepaired actions, tests passing) or `escalation_required` (something
    needed a human), and this — along with every step above — is written
    to the **Memory Manager**'s SQLite event log for that project ID.
13. **A structured report** (goal, every action's outcome, agent used,
    security status, test log, final status, project path) is returned.
    The CLI prints it as JSON; the dashboard has already been rendering
    each step of it live.

---

## Full file structure, with comments

```
agentos_project/
│
├── README.md                    # this file
├── requirements.txt              # requests, PyYAML, pytest, PyQt6
├── config.yaml.example           # copy to config.yaml to use a real LLM
│                                  # (see "Configuring LLM providers")
├── agentos_dashboard.py          # single-file PyQt6 desktop dashboard.
│                                  # Imports the agentos package below and
│                                  # runs CoreBrain on a background
│                                  # QThread, rendering every real event
│                                  # live (task queue, agent status,
│                                  # execution log, file explorer,
│                                  # memory/history table)
│
└── agentos/                      # the core engine package
    │
    ├── __init__.py               # package version marker
    ├── config.py                 # AgentOSConfig: loads config.yaml (or
    │                              # falls back to the built-in mock
    │                              # provider so the tool always runs);
    │                              # lets planner/worker roles each use a
    │                              # different provider+model
    ├── memory.py                 # MemoryManager — SQLite-backed project
    │                              # + event history (create_project, log,
    │                              # set_status, history) — the source of
    │                              # truth the dashboard's Memory/History
    │                              # tab reads from
    ├── planner.py                # Planner — sends the goal to the
    │                              # planner LLM, gets back a JSON plan
    │                              # tree (phases→milestones→tasks→
    │                              # subtasks→atomic_actions), validates
    │                              # it; flatten_atomic_actions() walks the
    │                              # tree into an ordered execution list
    ├── agents.py                 # BaseAgent + concrete agents:
    │                              #   CodingAgent       (+ role variants:
    │                              #                       backend/api/
    │                              #                       database/ui/
    │                              #                       refactoring)
    │                              #   TestingAgent
    │                              #   DocumentationAgent
    │                              #   DevOpsAgent
    │                              #   SecurityAgent     (regex static scan
    │                              #                       + LLM fix pass)
    │                              # _RestrictedToolProxy enforces each
    │                              # agent's tool permission whitelist.
    │                              # MultiAgentCoordinator routes each
    │                              # atomic action to the right agent
    │                              # instance based on its "agent" tag.
    ├── tools.py                  # ToolManager — the ONLY component that
    │                              # touches the filesystem/subprocess.
    │                              # create_folder / write_file /
    │                              # read_file / check_syntax (py_compile)
    │                              # / run_pytest. Every path is sandboxed:
    │                              # resolved and checked to still be
    │                              # inside the project workspace root.
    ├── verification.py           # VerificationEngine — real py_compile
    │                              # syntax check on generated .py files
    ├── testing.py                 # TestingEngine — thin wrapper that
    │                              # runs the real pytest suite via
    │                              # ToolManager.run_pytest
    ├── repair.py                  # RepairEngine — on any verification/
    │                              # test failure, sends the broken file +
    │                              # exact error to the worker LLM,
    │                              # rewrites the file with the response,
    │                              # re-verifies; loops up to
    │                              # max_repair_attempts then escalates
    ├── brain.py                   # CoreBrain — the orchestrator. run()
    │                              # implements the full step-by-step flow
    │                              # described above (plan → decompose →
    │                              # dispatch via MultiAgentCoordinator →
    │                              # verify → security scan → test →
    │                              # repair → log → report). Accepts an
    │                              # optional progress_cb(event) called
    │                              # after every step — this is exactly
    │                              # what the dashboard hooks into for
    │                              # live updates, with CoreBrain itself
    │                              # having zero knowledge that a UI exists
    ├── cli.py                     # command-line entry point:
    │                              #   python -m agentos.cli "<goal>"
    │                              #   python -m agentos.cli --demo
    │
    └── llm/                       # provider-agnostic LLM adapter layer
        │
        ├── __init__.py            # re-exports build_provider, REGISTRY
        ├── base.py                # LLMProvider abstract base + LLMResponse
        │                          # dataclass. generate_json() wraps
        │                          # generate() and enforces/parses a
        │                          # strict-JSON response — this is what
        │                          # the Planner uses to get a structured
        │                          # plan back from any provider
        ├── factory.py             # build_provider(spec) — reads a
        │                          # {"provider": "...", "model": "...", ...}
        │                          # dict from config.yaml and instantiates
        │                          # the matching provider class
        ├── anthropic_provider.py  # Claude via api.anthropic.com/v1/messages
        ├── openai_provider.py     # GPT via api.openai.com (OpenAI-compatible)
        ├── gemini_provider.py     # Gemini via generativelanguage.googleapis.com
        ├── openrouter_provider.py # Gateway to many hosted models
        │                          # (Claude/GPT/Gemini/Llama/Mistral/etc)
        │                          # via one OpenAI-compatible API
        ├── ollama_provider.py     # Fully OFFLINE/local — talks to a
        │                          # local Ollama server (default
        │                          # localhost:11434), no API key, no
        │                          # internet required
        ├── lmstudio_provider.py   # Fully OFFLINE/local — talks to LM
        │                          # Studio's local OpenAI-compatible
        │                          # server (default localhost:1234/v1)
        └── mock_provider.py       # Deterministic fake provider used by
                                   # `--demo` / blank-goal dashboard runs.
                                   # Returns a fixed, hand-written plan +
                                   # fixed, hand-written PyQt6 calculator
                                   # code so the ENTIRE pipeline (plan →
                                   # generate → verify → test → repair →
                                   # security scan) can be proven to work
                                   # with zero API keys. Swap for a real
                                   # provider the moment you have credentials.
```

---

## Setup

```bash
# 1. (recommended) create a virtual environment
python3 -m venv .venv
source .venv/bin/activate        # on Windows: .venv\Scripts\activate

# 2. install dependencies
pip install -r requirements.txt
```

`requirements.txt` installs: `requests` (all HTTP-based LLM providers),
`PyYAML` (config file parsing), `pytest` (the Testing Engine), and
`PyQt6` (generated UI apps + the dashboard itself).

---

## Running it — dashboard (recommended)

```bash
python agentos_dashboard.py
```

1. Leave **Goal** blank and click **Run** to use the built-in zero-setup
   demo (mock provider, no API key needed) — or type your own goal, e.g.
   *"Build a PyQt6 to-do list app"*, and a project name.
2. Watch, live:
   - **Model Manager** — which provider/model is set for planner vs. worker
   - **Agent Dashboard** — every specialized agent's current status
   - **Task Queue** tab — every atomic action, which agent handled it,
     its status, and any repair/security notes
   - **Execution Log** tab — a scrolling real-time log of every step
   - **Project Tree** tab — the actual generated files, live, once the
     run finishes
   - **Memory / History** tab — the real SQLite event log for that run
3. The status bar at the bottom shows the final outcome (`completed` or
   `escalation_required`) plus the path to the generated project.

---

## Running it — command line

```bash
# zero-setup demo (same mock provider, no API key)
python -m agentos.cli --demo

# your own goal, using whatever LLM you've configured in config.yaml
python -m agentos.cli "Build a PyQt6 markdown note-taking app" --project-name notes_app
```

The CLI prints the same structured JSON report the dashboard consumes,
plus the full `pytest` log.

---

## Configuring LLM providers

By default (no `config.yaml` present), AgentOS uses the built-in `mock`
provider so both the CLI and the dashboard always run out of the box.
To use a real model:

```bash
cp config.yaml.example config.yaml
```

Then edit it — **planner** and **worker** are configured independently,
so you can plan with a strong cloud model and generate code with a fast
local one, or any other combination:

```yaml
planner:
  provider: anthropic          # anthropic | openai | gemini | openrouter | ollama | lmstudio | mock
  model: claude-sonnet-4-6
  # api_key: sk-ant-...        # or set the matching env var instead (below)

worker:
  provider: openrouter
  model: meta-llama/llama-3.1-70b-instruct

max_repair_attempts: 3
workspace_dir: workspace
```

If `api_key` is omitted, each provider reads its key from an environment
variable instead:

| Provider     | Env var                 | Needs internet? |
|--------------|--------------------------|-----------------|
| anthropic    | `ANTHROPIC_API_KEY`      | yes             |
| openai       | `OPENAI_API_KEY`         | yes             |
| gemini       | `GEMINI_API_KEY`         | yes             |
| openrouter   | `OPENROUTER_API_KEY`     | yes             |
| ollama       | *(none — local server)*  | **no**          |
| lmstudio     | *(none — local server)*  | **no**          |
| mock         | *(none — built-in)*      | **no**          |

For fully offline use, run a local model with Ollama or LM Studio and
point `base_url` at it (defaults shown in `config.yaml.example`).

---

## The agents, and what each is restricted to

| Agent                | Handles action type(s)                | Allowed tool methods                                  |
|-----------------------|----------------------------------------|--------------------------------------------------------|
| `CodingAgent`          | `generate_file` (roles: general/backend/api/database/ui/refactoring) | `write_file`, `create_folder`, `read_file`, `check_syntax` |
| `TestingAgent`         | `generate_test`                        | `write_file`, `read_file`, `check_syntax`, `run_pytest` |
| `DocumentationAgent`   | `generate_doc`                         | `write_file`, `read_file`                               |
| `DevOpsAgent`          | `generate_config`                      | `write_file`, `read_file`, `create_folder`               |
| `SecurityAgent`        | post-generation scan on every `.py` file | `write_file`, `read_file`, `check_syntax`               |

Each restriction is enforced at runtime by `_RestrictedToolProxy` in
`agentos/agents.py` — an agent calling a method outside its whitelist
raises `PermissionError` immediately, rather than the restriction being
just a comment a future edit could silently break.

---

## Safety mechanisms already in place

- **Path sandboxing** (`agentos/tools.py`) — every file/folder path is
  resolved relative to the project's workspace root and rejected if the
  resolved path escapes it (blocks `../../etc/passwd`-style targets).
- **Per-agent tool permission whitelist** (`agentos/agents.py`) — see
  table above.
- **Static security scanning** (`SecurityAgent`) — deterministic regex
  scan for `eval()`, `exec()`, `os.system()`, `shell=True`, unsafe
  unpickling, unparameterized SQL — run on every generated Python file,
  independent of whatever the generating LLM "believes" is safe.
- **Verify-before-trust** — nothing is considered done just because an
  LLM wrote it: every file is syntax-checked, every project is actually
  tested with `pytest`, and failures escalate instead of being hidden.
- **Bounded repair loops** — `max_repair_attempts` (default 3) prevents
  infinite retry loops; unresolved failures are reported as
  `escalation_required`, never silently swallowed.

---

## What is NOT built yet

Being direct about the boundary rather than overstating scope:

- **Self-Critic Engine** — architecture/performance/scalability review
  passes beyond what the Security Agent already covers
- **Skill Manager** — create/store/update/combine/retire reusable skills,
  track skill performance over time
- **Multi-agent *parallel* coordination** — agents currently run
  sequentially per atomic action; true concurrent multi-agent execution
  isn't implemented
- **Resource-tiered execution presets** (low/mid/high-end machine profiles,
  quantized local models, parallel workers)
- **Hardened execution sandbox** — path-escape protection exists, but
  there's no OS-level process sandbox/container isolation yet
- **Plugin architecture** for future model/skill integration
- **Animated node-graph "mission control" visuals** — the dashboard is
  fully functional but deliberately plain; a richer visual theme is a
  cosmetic layer that can be added on top of the existing panels later
  without changing how the engine works

---

## Troubleshooting

- **`ValueError: ANTHROPIC_API_KEY not set`** (or similar for another
  provider) — you selected that provider in `config.yaml` without
  supplying a key. Either add `api_key:` under that role in `config.yaml`,
  or set the matching environment variable from the table above.
- **`PermissionError: Agent tried to call restricted tool method '...'`**
  — this means the restriction system is working as designed; it's not a
  bug to silence, it means an agent's code path tried to do something
  outside its intended scope.
- **Dashboard: "no config.yaml found -- using built-in mock provider"** —
  informational, not an error; copy `config.yaml.example` to `config.yaml`
  to use a real LLM instead.
- **Generated PyQt6 app won't display in a headless environment** — this
  is expected; PyQt6 apps need a display server. The dashboard and CLI
  themselves still run fine and the generated code is still verified and
  tested (tests are written to avoid instantiating `QApplication`/opening
  a window).

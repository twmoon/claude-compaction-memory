# claude-compaction-memory

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

Persistent memory for Claude Code that survives context compaction and session restarts. 100% local, no API key.

## Highlights

- **Survives compaction and session end** — two hooks (`PreCompact` + `SessionStart`) snapshot the recent conversation and re-inject it automatically on the next start.
- **No hot-path latency** — session start reads a plain-text cache only; the embedding model (torch) is never loaded inline. **~0.08s instead of ~13s.**
- **Two-tier recall** — a fast JSONL recency cache, backed by an async mem0 + Chroma semantic store for deeper lookups.
- **Local and keyless** — HuggingFace `all-MiniLM-L6-v2` embeddings + Chroma on disk. No OpenAI key, no daemon.
- **Path-scoped silos** — recall is isolated per working directory, so unrelated projects never bleed into one another.
- **Cross-platform** — Windows, Linux, and macOS.

## How it works

Claude Code compacts the conversation when context fills up, and drops it entirely when a session closes. Two hooks close that gap:

- **`PreCompact`** writes the recent conversation delta to disk right before compaction.
- **`SessionStart`** reads it back and injects it as `additionalContext` on every start, resume, or compact.

Recall is split into two tiers so the hot path stays instant:

| Tier | Store | Used by | Latency |
| --- | --- | --- | --- |
| 1 | plain-text JSONL recency cache | every session start | ~0.08s |
| 2 | mem0 + Chroma vector store | on-demand semantic recall | async, off the hot path |

The expensive embedding model loads only inside a detached background worker, so the hooks never block on it. mem0 alone tends to be forgotten by the model; hooks alone vanish when the session ends — combining them removes both failure modes.

## Installation

```bash
git clone https://github.com/twmoon/claude-compaction-memory.git
cd claude-compaction-memory
```

```powershell
.\install.ps1      # Windows
```

```bash
./install.sh       # Linux / macOS
```

The installer creates an isolated virtualenv, installs pinned dependencies (CPU-only torch), pre-downloads the embedding model, and prints a ready-to-paste hook snippet.

## Configuration

Add the printed snippet to the `"hooks"` block of `~/.claude/settings.json` (the installer fills in the absolute paths):

```json
{
  "hooks": {
    "PreCompact": [
      { "matcher": "manual|auto",
        "hooks": [{ "type": "command", "timeout": 45, "command": "<venv-python> save.py" }] }
    ],
    "SessionStart": [
      { "matcher": "startup|resume|compact",
        "hooks": [{ "type": "command", "timeout": 45, "command": "<venv-python> restore.py" }] }
    ]
  }
}
```

> Hooks take effect after you **restart Claude Code**.

## Project layout

| File | Role |
| --- | --- |
| `mem_lib.py` | mem0 wrapper (keyless) + cache helpers + silo-key logic |
| `save.py` | `PreCompact` hook — write cache, spawn async ingest |
| `restore.py` | `SessionStart` hook — inject the 6 most recent entries |
| `ingest.py` | detached Tier-2 worker |
| `braindump.py` | manual "remember this now" capture |

## Silos

Recall is keyed by the **full working-directory path**, not its basename — so `ctf-a/web1` and `ctf-b/web1` never merge. Drop an empty `.memroot` file in a parent directory to group everything beneath it into a single silo.

## Notes

- **Never call mem0 directly on the hot path.** `get_memory()` pulls in torch (~13s). New hooks should read the cache and push ingestion to `spawn_ingest` in the background.
- **mem0 2.0.4:** pass `filters={"user_id": ...}` to `search()` / `get_all()`. A top-level `user_id` is silently ignored and returns nothing.
- **Don't build the venv with `--system-site-packages`.** A leaked system/anaconda NumPy ABI causes `dtype size 96 vs 88` crashes. The installer builds a fully isolated env with CPU-only torch.

## License

[MIT](./LICENSE)

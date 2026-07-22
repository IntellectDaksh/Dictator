# Contributing

This is a solo daily-driver tool, kept intentionally small. Bug reports and
focused fixes are welcome; large feature PRs may not fit the maintenance
scope of a single-file app — open an issue first to discuss before writing
a lot of code.

## Dev setup

```powershell
git clone https://github.com/IntellectDaksh/Dictator.git
cd Dictator
.\scripts\install.ps1
```

## Before submitting a PR

- `python -m py_compile main.py` must pass.
- `python main.py --selftest` must pass (requires a local Ollama running).
- Keep changes inside `main.py`'s existing section banners (`# ---- name`) —
  see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the code map and
  threading rules before touching the dashboard or session state.
- No new dependencies unless the stdlib and existing deps genuinely can't do
  it — this app stays lightweight on purpose.

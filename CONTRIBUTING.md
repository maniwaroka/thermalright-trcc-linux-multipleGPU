# Contributing to TRCC Linux

Thanks for your interest in contributing! This project is a Linux port of the Thermalright LCD Control Center and welcomes bug fixes, device support, hardware testing, and documentation improvements.

## Development Setup

```bash
git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e '.[dev]'
trcc setup              # interactive wizard — checks deps, udev, desktop entry
```

Or manually:

```bash
trcc setup-udev         # install udev rules (auto-prompts for sudo)
# Unplug/replug USB cable after
```

## Running Tests and Linting

```bash
PYTHONPATH=src pytest tests/ -x -q   # run tests
pytest --cov                         # run with coverage
ruff check .                         # lint
npx pyright                          # type check
```

All PRs must pass tests, `ruff check`, and `pyright` with 0 errors.

## Branch Strategy

1. Fork the repo and create a branch off `stable`
2. Make your changes and ensure tests pass
3. Open a PR targeting `stable`

> `stable` is the default branch. All development, releases, and user-facing clones happen here.

## Ways to Contribute

- **Bug fixes** — Reproduce, write a test, fix it
- **Device support** — Add new Thermalright USB VID:PID mappings to `adapters/device/detector.py`
- **Hardware testing** — Own a HID device? See [doc/DEVICE_TESTING.md](doc/DEVICE_TESTING.md) for how to help validate support
- **Documentation** — Install guides, troubleshooting tips, translations

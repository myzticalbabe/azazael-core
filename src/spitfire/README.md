# Project SpitFire — TswArm Standalone Executable

Multi-target standalone build of the TswArm tree-swarming tool with backward compatibility to legacy operating systems.

## Targets

| Target | OS | Python | Compiler | Threading |
|--------|-----|--------|----------|-----------|
| `win7` | Windows 7 / Server 2008R2 | 3.8 | MSVC 14.1 (VS 2017) | GIL |
| `win81` | Windows 8.1 / Server 2012R2 | 3.12 | MSVC 14.2 (VS 2019) | GIL |
| `win10` | Windows 10 1607+ | 3.15t | MSVC 14.3 (VS 2022) | **Free-threaded** |
| `macos` | macOS 10.15 Catalina+ | 3.15t | Clang (universal2) | **Free-threaded** |
| `linux` | glibc 2.17+ (RHEL 7, Ubuntu 16.04+) | 3.15t | GCC/Clang | **Free-threaded** |

## Architecture: GIL-Sidecar Pattern

```
┌─────────────────────────────────────────────────────────────┐
│                    SpitFire Launcher                         │
│  • Detects OS version at runtime                            │
│  • Dispatches to appropriate implementation                 │
└─────────────────────────────────────────────────────────────┘
         │                              │
    ┌────▼────┐                    ┌─────▼─────┐
    │ impl_gil │                    │ impl_ft   │
    │ Python    │                    │ Python    │
    │ 3.8/3.12  │                    │ 3.15t     │
    │ + GIL     │                    │ free-thrd │
    └───────────┘                    └───────────┘
```

Core logic is compiled once per target; only the threading implementation differs.

## Quick Start

### Build All Targets (CI)
```bash
# Triggered automatically on push to main/azazael-intel-ultra-9-evo
# Artifacts uploaded as GitHub Actions artifacts
```

### Local Build (Windows)
```bash
# Win7 target (requires Python 3.8 + VS 2017)
C:\Python38\python.exe src/spitfire/build/build_all.py --target win7

# Win8.1 target (requires Python 3.12 + VS 2019)
C:\Python312\python.exe src/spitfire/build/build_all.py --target win81

# Win10+ target (requires Python 3.15t free-threaded + VS 2022)
C:\Azazael\.venvs\py315t\Scripts\python.exe src/spitfire/build/build_all.py --target win10
```

### Local Build (macOS/Linux)
```bash
# macOS (requires Python 3.15t universal2 from python.org)
python3.15t src/spitfire/build/build_all.py --target macos

# Linux (requires Python 3.15t on manylinux2014 distro)
python3.15t src/spitfire/build/build_all.py --target linux
```

## Usage

```bash
# Run dirty-worktree census (read-only)
SpitFire-win10.exe census --max-workers 32 --save census.jsonl

# Apply suppressions to flagged files
SpitFire-win10.exe fix --file path/to/file.py

# Run both
SpitFire-win10.exe all
```

## Nuitka Configurations

Per-target configs in `src/spitfire/build/`:
- `spitfire.win7.nuitka.toml`
- `spitfire.win81.nuitka.toml`
- `spitfire.win10.nuitka.toml`
- `spitfire.macos.nuitka.toml`
- `spitfire.linux.nuitka.toml`

## Requirements

- **Nuitka 4.2+** for Python 3.15t experimental support
- **Visual Studio 2017/2019/2022** for Windows targets (matching MSVC version)
- **Python 3.15t free-threaded** from python.org or self-compiled
- **Git** for dirty-worktree detection

## License

Part of Azazael project — Apache 2.0
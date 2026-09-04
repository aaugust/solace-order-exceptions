"""Connection profile loading.

Selects which broker everything talks to. One import, at the top of anything
that connects, and the whole project follows the same profile.

    import profile
    profile.load()          # honours DEMO_PROFILE, defaults to cloud

Profiles live in .env.local (committed, no secrets) and .env.cloud (gitignored,
real credentials). Switch with the environment variable:

    $env:DEMO_PROFILE = "cloud"
    .\\demo-up.ps1

WHY NOT python-dotenv
It is not installed in this venv and it is not worth a dependency for twenty
lines. More importantly, the stdlib version can be BOM-tolerant, which matters
here: PowerShell's `Set-Content -Encoding utf8` writes a UTF-8 BOM, so the first
line of a file written that way begins with a zero-width character. A naive
parser reads the first key as "\\ufeffSOLACE_HOST" and silently ignores it. That
already cost time once in this project when a `grep '^GEMINI_API_KEY='` reported
a key missing that was present. utf-8-sig strips it.

EXISTING ENVIRONMENT WINS
Values already set in the environment are not overwritten, so a one-off
override on the command line beats the file rather than being silently
discarded.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_loaded: str | None = None


def load(name: str | None = None, quiet: bool = False) -> str:
    """Load a profile into os.environ. Returns the profile name used."""
    global _loaded
    # DEFAULT CLOUD, matching demo-up.ps1. It used to default to local, which
    # meant any entry point started without DEMO_PROFILE set silently aimed at
    # a broker that is no longer running - the failure surfaced as "Connection
    # refused (10061)" from the Solace client, which reads as a broker fault
    # rather than a configuration one. Two defaults for the same choice is the
    # bug; there is now one. Found 2026-09-02.
    name = name or os.environ.get("DEMO_PROFILE", "cloud")
    path = ROOT / f".env.{name}"

    if not path.exists():
        raise FileNotFoundError(
            f"No profile at {path}. Expected .env.local or .env.cloud in "
            f"{ROOT}. For cloud, fill in .env.cloud from the Solace Cloud "
            f"console (Connect tab for messaging, Manage for SEMP)."
        )

    applied, blank = 0, []
    # utf-8-sig: see the BOM note in the module docstring.
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not value:
            blank.append(key)
            continue
        if key not in os.environ:
            os.environ[key] = value
            applied += 1

    if blank:
        raise ValueError(
            f"{path.name} has empty values for: {', '.join(blank)}.\n"
            f"Fill them in from the Solace Cloud console before connecting."
        )

    _loaded = name
    if not quiet:
        # Say which broker we are pointed at, every time. Publishing to the
        # wrong one mid-demo is a confusing failure, and one line prevents it.
        host = os.environ.get("SOLACE_HOST", "?")
        print(f"[profile: {name}]  {host}")
    return name


def active() -> str | None:
    """The profile loaded in this process, if any."""
    return _loaded

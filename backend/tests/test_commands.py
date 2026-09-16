"""Every CLI command `commands.py` defines has to be registered on the app.

The worker service's whole entry point is `flask --app app tailoring-worker`. It was written,
tested through its own functions, and never wired into `app.py` — so the command did not
exist, and the first thing that noticed was a crash loop in production. A function nobody
calls is invisible to a test suite that imports the function directly.
"""

import re
import pathlib

from app import app


def registrars():
    """The `register_*` functions `commands.py` defines."""
    source = (pathlib.Path(__file__).resolve().parent.parent / "commands.py").read_text()
    return set(re.findall(r"^def (register_\w+)\(app\)", source, re.M))


def declared_commands():
    """Command names those functions declare, as `@app.cli.command("name")`."""
    source = (pathlib.Path(__file__).resolve().parent.parent / "commands.py").read_text()
    return set(re.findall(r'@app\.cli\.command\("([\w-]+)"\)', source))


def test_every_registrar_is_called():
    wired = (pathlib.Path(__file__).resolve().parent.parent / "app.py").read_text()
    missing = [name for name in registrars() if f"{name}(app)" not in wired]

    assert not missing, f"defined in commands.py but never called in app.py: {missing}"


def test_every_declared_command_is_actually_available():
    available = set(app.cli.commands)
    missing = declared_commands() - available

    assert not missing, f"declared but not reachable from the CLI: {missing}"


def test_the_worker_entry_point_exists():
    """Named on its own because it is the one a deploy depends on."""
    assert "tailoring-worker" in app.cli.commands

"""Double-click launch behaviour.

A frozen exe started from Explorer gets no arguments, and the console closes as
soon as the process exits, so "exit 2 with a usage error" was indistinguishable
from a crash. These tests pin the replacement behaviour.
"""

from __future__ import annotations

import argparse
import socket
import sys

import pytest

from srm_open.api import cli


def test_launch_default_starts_the_web_ui(monkeypatch):
    seen = {}

    def fake_serve(args):
        seen["args"] = args
        return 0

    monkeypatch.setattr(cli, "cmd_serve", fake_serve)

    assert cli._launch_default() == 0
    assert seen["args"].open_browser is True
    assert seen["args"].host == "127.0.0.1"
    assert seen["args"].port == 8000


def test_serve_without_the_api_extra_explains_itself(monkeypatch, capsys):
    """The 3.11+ test matrix installs [dev,viz], not [dev,api]."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name == "uvicorn":
            raise ImportError("no uvicorn")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert cli.cmd_serve(argparse.Namespace(
        host="127.0.0.1", port=8000, log_level="warning",
        open_browser=False)) == 2
    assert "pip install -e '.[api]'" in capsys.readouterr().err


def test_main_with_no_arguments_uses_the_launch_default(monkeypatch):
    monkeypatch.setattr(cli, "_launch_default", lambda: 0)
    assert cli.main([]) == 0


def test_no_arguments_does_not_print_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_launch_default", lambda: 0)

    cli.main([])

    assert "required: command" not in capsys.readouterr().err


def test_a_bad_subcommand_still_fails_loudly(capsys):
    """The GUI fallback must not swallow genuine usage errors."""
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["not-a-command"])
    assert excinfo.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_port_probe_detects_a_busy_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert cli._port_is_free("127.0.0.1", port) is False

    assert cli._port_is_free("127.0.0.1", port) is True


@pytest.fixture
def fake_web(monkeypatch):
    """Stub the web stack so these tests run without the ``api`` extra.

    cmd_serve imports both lazily at call time, so modules in sys.modules are
    enough; the real ones are never imported.
    """
    import types

    uvicorn = types.ModuleType("uvicorn")
    uvicorn.run = lambda *a, **kw: None
    rest = types.ModuleType("srm_open.api.rest")
    rest.frontend_available = lambda: True
    monkeypatch.setitem(sys.modules, "uvicorn", uvicorn)
    monkeypatch.setitem(sys.modules, "srm_open.api.rest", rest)
    return uvicorn


def test_serve_walks_forward_when_the_port_is_taken(monkeypatch, fake_web):
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: port != 8000)

    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(fake_web, "run", fake_run)

    assert cli.cmd_serve(argparse.Namespace(
        host="127.0.0.1", port=8000, log_level="warning",
        open_browser=False)) == 0
    assert seen["port"] == 8001


def test_serve_gives_up_when_the_whole_range_is_taken(monkeypatch, capsys,
                                                      fake_web):
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: False)

    assert cli.cmd_serve(argparse.Namespace(
        host="127.0.0.1", port=8000, log_level="warning",
        open_browser=False)) == 2
    assert "all busy" in capsys.readouterr().err


def test_crash_log_falls_back_when_the_exe_folder_is_readonly(
        monkeypatch, tmp_path):
    readonly = tmp_path / "program-files"
    readonly.mkdir()
    monkeypatch.setattr(sys, "executable", str(readonly / "srm.exe"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    real_open = open

    def fake_open(path, *a, **kw):
        if str(path).startswith(str(readonly)):
            raise OSError("read-only")
        return real_open(path, *a, **kw)

    monkeypatch.setattr("builtins.open", fake_open)

    log = cli._crash_log_path()
    assert log.parent != readonly
    assert log.parent.exists()


def test_crash_report_writes_a_log_and_returns_one(monkeypatch, tmp_path):
    log = tmp_path / "srm-error.log"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli, "_crash_log_path", lambda: log)
    monkeypatch.setattr(cli, "_pause_if_interactive", lambda: None)
    monkeypatch.setattr(cli, "build_parser", lambda: _parser_that_raises())

    assert cli.main(["forward"]) == 1
    assert "kaboom" in log.read_text(encoding="utf-8")


def test_an_exception_is_not_swallowed_when_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(cli, "build_parser", lambda: _parser_that_raises())

    with pytest.raises(RuntimeError, match="kaboom"):
        cli.main(["forward"])


def _parser_that_raises():
    """Minimal stand-in for build_parser(): one subcommand that always fails."""
    def boom(args):
        raise RuntimeError("kaboom")

    parser = argparse.ArgumentParser(prog="srm")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("forward").set_defaults(func=boom)
    return parser

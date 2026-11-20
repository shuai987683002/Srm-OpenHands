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
    monkeypatch.setattr("srm_open.api.rest.frontend_available", lambda: True)

    assert cli._launch_default() == 0
    assert seen["args"].open_browser is True
    assert seen["args"].host == "127.0.0.1"
    assert seen["args"].port == 8000


def test_launch_default_prints_help_when_the_gui_is_missing(monkeypatch, capsys):
    monkeypatch.setattr("srm_open.api.rest.frontend_available", lambda: False)

    assert cli._launch_default() == 2
    assert "usage: srm" in capsys.readouterr().out


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


def test_serve_walks_forward_when_the_port_is_taken(monkeypatch):
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: port != 8000)
    monkeypatch.setattr("srm_open.api.rest.frontend_available", lambda: True)

    seen = {}

    def fake_run(*args, **kwargs):
        seen.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)

    assert cli.cmd_serve(argparse.Namespace(
        host="127.0.0.1", port=8000, log_level="warning",
        open_browser=False)) == 0
    assert seen["port"] == 8001


def test_serve_gives_up_when_the_whole_range_is_taken(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_port_is_free", lambda host, port: False)
    monkeypatch.setattr("srm_open.api.rest.frontend_available", lambda: True)

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

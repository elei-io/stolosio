import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "runtime" / "browser-launch.py"
SPEC = importlib.util.spec_from_file_location("browser_launch", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
browser_launch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(browser_launch)


def test_transfers_browserless_session_tmpdir(monkeypatch):
    scratch = Path("/tmp/browserless-scratch-dirs") / uuid4().hex / "session"
    chowned = []
    monkeypatch.setattr(
        browser_launch.os,
        "chown",
        lambda path, uid, gid: chowned.append((path, uid, gid)),
    )
    try:
        assert browser_launch.transfer_browserless_tmpdir(str(scratch), 1001, 1002)
        assert scratch.is_dir()
        assert chowned == [(scratch, 1001, 1002)]
    finally:
        scratch.rmdir()
        scratch.parent.rmdir()


def test_does_not_transfer_tmpdir_outside_browserless_prefix(tmp_path, monkeypatch):
    scratch = tmp_path / "untrusted"
    chowned = []
    monkeypatch.setattr(browser_launch.os, "chown", lambda *args: chowned.append(args))

    assert not browser_launch.transfer_browserless_tmpdir(str(scratch), 1001, 1002)
    assert not scratch.exists()
    assert chowned == []


@pytest.mark.parametrize(
    "tmpdir",
    ["/tmp/browserless-scratch-dirs-escape/session", "/tmp/browserless-scratch-dirs"],
)
def test_does_not_transfer_prefix_sibling_or_root(tmpdir, monkeypatch):
    chowned = []
    monkeypatch.setattr(browser_launch.os, "chown", lambda *args: chowned.append(args))

    assert not browser_launch.transfer_browserless_tmpdir(tmpdir, 1001, 1002)
    assert chowned == []

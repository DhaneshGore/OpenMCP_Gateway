"""Unit tests for packages/environments.py helpers."""

from __future__ import annotations

import sys

import pytest

from openmcp_gateway.packages.environments import gateway_site_paths, system_read_paths


def test_gateway_site_paths_returns_list_of_existing_dirs() -> None:
    paths = gateway_site_paths()
    assert isinstance(paths, list)
    assert len(paths) >= 1
    for p in paths:
        assert isinstance(p, str)


def test_gateway_site_paths_are_sorted_and_unique() -> None:
    paths = gateway_site_paths()
    assert paths == sorted(set(paths))


def test_gateway_site_paths_includes_user_site_when_on_sys_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pytest.TempdirFactory
) -> None:
    """Packages installed with `pip install --user` live in the user site-packages
    directory, which is excluded when Python runs with -I (isolated mode).
    gateway_site_paths() must include that directory in the .pth file so that
    sandboxed package servers can import them.

    Regression test for: user site-packages missing from gateway_site_paths()
    causing ModuleNotFoundError inside sandboxed package servers.
    """
    import site

    if not hasattr(site, "getusersitepackages"):
        pytest.skip("site.getusersitepackages not available")

    # Use a real (temporary) directory so the os.path.isdir() check passes.
    fake_user_site = str(tmp_path)
    monkeypatch.setattr(site, "getusersitepackages", lambda: fake_user_site)

    original_path = list(sys.path)

    # When the fake user site is NOT on sys.path, it must not be included.
    monkeypatch.setattr(sys, "path", [p for p in original_path if p != fake_user_site])
    paths_without = gateway_site_paths()
    assert fake_user_site not in paths_without

    # When the fake user site IS on sys.path, it must be included.
    monkeypatch.setattr(sys, "path", [fake_user_site, *original_path])
    paths_with = gateway_site_paths()
    assert fake_user_site in paths_with


def test_system_read_paths_contains_usr() -> None:
    paths = system_read_paths()
    assert any(p.startswith("/usr") for p in paths)


def test_system_read_paths_contains_dev_null() -> None:
    paths = system_read_paths()
    assert "/dev/null" in paths

"""Archive validation, canonicalisation and safe extraction."""

from __future__ import annotations

import os
import stat
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from openmcp_gateway.config import PackageLimits
from openmcp_gateway.packages.archive import (
    IntegrityError,
    PackageValidationError,
    archive_directory,
    extract_verified,
    inspect_archive,
)
from openmcp_gateway.packages.dependencies import parse_lock
from tests.packaging_helpers import (
    DEFAULT_POLICY,
    WEATHER,
    metadata,
    symlink_zip,
    weather_entries,
    zip_dir,
    zip_entries,
)

HASH = "a" * 64


def errors_for(data: bytes, policy=DEFAULT_POLICY) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(PackageValidationError) as info:
        inspect_archive(data, policy)
    return " | ".join(info.value.errors)


def test_fixture_validates_with_full_structure() -> None:
    pkg = inspect_archive(zip_dir(WEATHER), DEFAULT_POLICY)
    paths = {f.path for f in pkg.files}
    assert {
        "server.py",
        "metadata.json",
        "tools/__init__.py",
        "tools/forecast.py",
        "utils/api_client.py",
        "schemas/weather.json",
        "config/defaults.yaml",
        "templates/report.txt",
        "requirements.txt",
    } <= paths
    assert pkg.metadata.name == "weather-mcp"
    assert pkg.lock_digest is None


def test_top_level_folder_is_stripped_and_digest_is_canonical() -> None:
    plain = inspect_archive(zip_dir(WEATHER), DEFAULT_POLICY)
    nested = inspect_archive(zip_dir(WEATHER, "weather-mcp/", reverse=True), DEFAULT_POLICY)
    stored = inspect_archive(zip_dir(WEATHER, method=zipfile.ZIP_STORED), DEFAULT_POLICY)
    assert plain.digest == nested.digest == stored.digest
    assert [f.path for f in plain.files] == [f.path for f in nested.files]


def test_changed_content_changes_digest() -> None:
    a = inspect_archive(zip_entries(weather_entries()), DEFAULT_POLICY)
    b = inspect_archive(zip_entries(weather_entries(**{"utils/helpers.py": "X = 1\n"})), DEFAULT_POLICY)
    assert a.digest != b.digest


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("../evil.py", "traversal"),
        ("tools/../../evil.py", "traversal"),
        ("/etc/evil.py", "absolute path"),
        ("C:/evil.py", "absolute path"),
        ("tools\\..\\evil.py", "backslash"),
        ("tools/./x.py", "traversal"),
        ("tools//x.py", "empty component"),
        ("bad\x01name.py", "control characters"),
        ("/".join(["d"] * 20) + "/x.py", "too deep"),
        ("a" * 300 + ".py", "too long"),
    ],
)
def test_malicious_paths_are_rejected(name: str, message: str) -> None:
    assert message in errors_for(zip_entries({**weather_entries(), name: "x = 1\n"}))


def test_symlinks_are_rejected() -> None:
    assert "symbolic links" in errors_for(symlink_zip("link.py", "/etc/passwd"))


def test_special_files_are_rejected() -> None:
    import io

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as zf:
        for name, body in weather_entries().items():
            zf.writestr(name, body)
        info = zipfile.ZipInfo("fifo.txt")
        info.external_attr = (stat.S_IFIFO | 0o644) << 16
        zf.writestr(info, "")
    assert "special files" in errors_for(out.getvalue())


def test_duplicates_and_case_collisions_are_rejected() -> None:
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns about duplicate names
        import io

        out = io.BytesIO()
        with zipfile.ZipFile(out, "w") as zf:
            for name, body in weather_entries().items():
                zf.writestr(name, body)
            zf.writestr("server.py", "print('shadow')\n")
    assert "duplicate path" in errors_for(out.getvalue())
    assert "duplicate path" in errors_for(zip_entries({**weather_entries(), "Server.py": "x = 1\n"}))


def test_disallowed_file_types() -> None:
    for name in ("native.so", "run.sh", "cached.pyc", "tool.exe", "noext"):
        assert "not permitted" in errors_for(zip_entries({**weather_entries(), name: b"\x7fELF"}))


def test_zip_bomb_ratio_and_size_limits() -> None:
    bomb = zip_entries({**weather_entries(), "big.txt": b"0" * 5_000_000})
    assert "compression ratio" in errors_for(bomb)
    small = replace(DEFAULT_POLICY, limits=PackageLimits(max_file_bytes=1000, max_compression_ratio=1000))
    assert "too large" in errors_for(zip_entries({**weather_entries(), "big.txt": "x" * 5000}), small)
    few = replace(DEFAULT_POLICY, limits=PackageLimits(max_files=3))
    assert "too many" in errors_for(zip_entries(weather_entries()), few)
    tiny = replace(DEFAULT_POLICY, limits=PackageLimits(max_archive_bytes=1024))
    assert "larger than" in errors_for(zip_entries(weather_entries()), tiny)


def test_declared_size_lies_are_caught() -> None:
    data = bytearray(zip_entries(weather_entries(), method=zipfile.ZIP_STORED))
    # Corrupt one byte of file content: the CRC check must fail.
    idx = data.find(b"WeatherReading")
    data[idx] ^= 0xFF
    assert "corrupt entry" in errors_for(bytes(data))


def test_not_a_zip() -> None:
    assert "not a valid ZIP" in errors_for(b"this is not a zip file")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"metadata.json": None}, "metadata.json is missing"),
        ({"metadata.json": "{not json"}, "not valid UTF-8 JSON"),
        ({"metadata.json": metadata(name="Bad Name")}, "name must match"),
        ({"metadata.json": metadata(version="latest")}, "semantic version"),
        ({"metadata.json": metadata(entrypoint="../server.py")}, "relative path inside"),
        ({"metadata.json": metadata(entrypoint="missing.py")}, "does not exist"),
        ({"metadata.json": metadata(entrypoint="README.md")}, "must be a .py file"),
        ({"metadata.json": metadata(unknown_field=1)}, "Extra inputs"),
        ({"tools/weather.py": "def broken(:\n"}, "syntax error in 'tools/weather.py'"),
        ({"requirements.txt": "requests>=2\n"}, "lock file"),
        ({"requirements.lock": "requests==2.0\n"}, "expected 'name==version"),
        ({"requirements.lock": f"--index-url https://evil.example\nx==1 --hash=sha256:{HASH}\n"}, "options"),
    ],
)
def test_metadata_entrypoint_and_dependency_errors(overrides: dict[str, str | None], message: str) -> None:
    assert message in errors_for(zip_entries(weather_entries(**overrides)))


def test_dependency_policies() -> None:
    lock = f"tinydep==1.0.0 \\\n    --hash=sha256:{HASH}\n"
    entries = weather_entries(**{"requirements.lock": lock})
    ok = inspect_archive(zip_entries(entries), DEFAULT_POLICY)
    assert ok.lock_digest is not None
    none = replace(DEFAULT_POLICY, dependency_policy="none")
    assert "does not install package dependencies" in errors_for(zip_entries(entries), none)
    allow = replace(DEFAULT_POLICY, allowed_projects=frozenset({"requests"}))
    assert "not in the allowed project list" in errors_for(zip_entries(entries), allow)


def test_lock_parser() -> None:
    reqs, errors = parse_lock(
        "# comment\n"
        f"Foo_Bar[extra]==1.2.3 ; python_version >= '3.11' \\\n    --hash=sha256:{HASH} \\\n"
        f"    --hash=sha256:{'b' * 64}\n"
        f"baz==0.1 --hash=sha256:{HASH}  # via foo\n"
    )
    assert errors == []
    assert [(r.name, r.version, len(r.hashes)) for r in reqs] == [("foo-bar", "1.2.3", 2), ("baz", "0.1", 1)]
    _, errors = parse_lock(f"a==1 --hash=sha256:{HASH}\na==2 --hash=sha256:{HASH}\n-e .\nb>=1\n")
    assert len(errors) == 3


def test_archive_directory_refuses_symlinks(tmp_path: Path) -> None:
    import shutil

    pkg = tmp_path / "pkg"
    shutil.copytree(WEATHER, pkg)
    os.symlink("/etc/passwd", pkg / "leak.txt")
    with pytest.raises(PackageValidationError, match="symbolic links"):
        archive_directory(pkg, PackageLimits())


def test_extract_verified_restores_tree_read_only(tmp_path: Path) -> None:
    pkg = inspect_archive(zip_dir(WEATHER, "weather-mcp/"), DEFAULT_POLICY)
    artifact = tmp_path / "a.zip"
    artifact.write_bytes(pkg.artifact)
    dest = tmp_path / "out"
    extract_verified(artifact, pkg.digest, pkg.files, dest, DEFAULT_POLICY)
    for f in pkg.files:
        restored = dest / f.path
        assert restored.read_bytes() == (WEATHER / f.path).read_bytes()
        assert not os.access(restored, os.W_OK)
    assert (dest / "tools" / "forecast.py").is_file()
    with pytest.raises(PermissionError):
        (dest / "new.py").write_text("x")


def test_extract_verified_rejects_checksum_and_manifest_mismatch(tmp_path: Path) -> None:
    pkg = inspect_archive(zip_dir(WEATHER), DEFAULT_POLICY)
    artifact = tmp_path / "a.zip"
    artifact.write_bytes(pkg.artifact)
    with pytest.raises(IntegrityError, match="checksum mismatch"):
        extract_verified(artifact, "0" * 64, pkg.files, tmp_path / "x", DEFAULT_POLICY)
    tampered = [f.model_copy(update={"sha256": "0" * 64}) if f.path == "server.py" else f for f in pkg.files]
    with pytest.raises(IntegrityError, match=r"server\.py"):
        extract_verified(artifact, pkg.digest, tampered, tmp_path / "y", DEFAULT_POLICY)
    with pytest.raises(IntegrityError, match="manifest"):
        extract_verified(artifact, pkg.digest, pkg.files[1:], tmp_path / "z", DEFAULT_POLICY)

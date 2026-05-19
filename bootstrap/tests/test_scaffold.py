"""Smoke test: the bootstrap package and its test package are importable."""


def test_bootstrap_package_imports():
    import bootstrap

    assert bootstrap is not None


def test_requirements_file_exists():
    from pathlib import Path

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    assert req.is_file(), "bootstrap/requirements.txt must exist"
    assert "pytest" in req.read_text()

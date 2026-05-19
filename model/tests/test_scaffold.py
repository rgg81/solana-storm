"""Smoke test: the model package and its test package are importable."""


def test_model_package_imports():
    import model

    assert model is not None


def test_requirements_file_lists_the_core_libs():
    from pathlib import Path

    req = Path(__file__).resolve().parents[1] / "requirements.txt"
    assert req.is_file(), "model/requirements.txt must exist"
    text = req.read_text()
    for lib in ("pandas", "numpy", "lightgbm", "scikit-learn", "matplotlib", "pytest"):
        assert lib in text, f"requirements.txt must list {lib}"

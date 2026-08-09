"""Guards the "a fresh clone runs" exit condition.

The failure mode this catches happened once already: a package installed by hand
into the local venv, imported by committed code, never added to requirements.txt.
Everything passes locally and a fresh clone dies on import. Nothing else in the
suite notices, because the suite runs in the venv that has the package.

Deliberately parses requirements.txt as text rather than asking the environment
what is installed -- the installed set is the thing under suspicion.
"""

import ast
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parent.parent

# Directories whose imports are not runtime dependencies of the app.
EXCLUDED_DIRS = {"venv", "__pycache__", ".pytest_cache", "tests", "scripts"}

# Import name -> distribution name, for the cases where they differ.
IMPORT_TO_DISTRIBUTION = {
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jwt": "pyjwt",
    "multipart": "python-multipart",
    "dateutil": "python-dateutil",
    "yaml": "pyyaml",
}


def _normalize(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _runtime_source_files() -> list[pathlib.Path]:
    return [
        p
        for p in BACKEND.rglob("*.py")
        if not EXCLUDED_DIRS & set(p.relative_to(BACKEND).parts)
    ]


def _first_party_names() -> set[str]:
    """Top-level modules and packages that live in backend/ itself."""
    return {p.relative_to(BACKEND).parts[0].removesuffix(".py") for p in _runtime_source_files()}


def _imported_top_level() -> dict[str, set[str]]:
    """Top-level import name -> the files that import it."""
    imports: dict[str, set[str]] = {}
    for path in _runtime_source_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                imports.setdefault(name, set()).add(path.relative_to(BACKEND).as_posix())
    return imports


def _declared_distributions(filename: str) -> set[str]:
    text = (BACKEND / filename).read_text(encoding="utf-8")
    declared = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        declared.add(_normalize(line.split("==")[0].split(">=")[0]))
    return declared


def test_every_runtime_import_is_declared():
    declared = _declared_distributions("requirements.txt")
    first_party = _first_party_names()
    stdlib = sys.stdlib_module_names

    undeclared = {}
    for module, sources in _imported_top_level().items():
        if module in stdlib or module in first_party:
            continue
        distribution = _normalize(IMPORT_TO_DISTRIBUTION.get(module, module))
        if distribution not in declared:
            undeclared[module] = (distribution, sorted(sources))

    assert not undeclared, "\n".join(
        f"{module!r} (needs {dist!r} in requirements.txt) imported by {', '.join(srcs)}"
        for module, (dist, srcs) in sorted(undeclared.items())
    )


def test_requirements_are_pinned():
    """Unpinned dependencies make 'it worked last week' unreproducible, which
    undermines every measurement taken against them."""
    for filename in ("requirements.txt", "requirements-dev.txt"):
        for line in (BACKEND / filename).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "-")):
                continue
            assert "==" in line, f"{filename}: {line!r} is not pinned to an exact version"


def test_dev_only_packages_are_not_in_runtime_requirements():
    """pytest in requirements.txt means deploying the test framework to prod and
    hides which packages the app actually needs."""
    runtime = _declared_distributions("requirements.txt")
    assert "pytest" not in runtime
    assert "tiktoken" not in runtime, (
        "tiktoken is measurement tooling; move it to requirements.txt only when "
        "the chunker needs it at runtime"
    )

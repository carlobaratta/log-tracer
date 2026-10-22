"""The dependency rules of ARCHITECTURE.md §4, checked on the source code.

Every module must belong to a layer listed in ``LAYERS``; each layer declares
the internal modules and third-party packages it may import. Adding a module
outside the known layers, or an import that crosses a boundary, fails here.
"""

import ast
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import log_tracer

PACKAGE_DIR = Path(log_tracer.__file__).parent
CORE = ("log_tracer.models", "log_tracer.config", "log_tracer.errors")


@dataclass(frozen=True)
class Layer:
    internal: tuple[str, ...]
    """Internal modules (and their submodules) that may be imported."""
    third_party: tuple[str, ...] = ()


ANYTHING = ("log_tracer",)

LAYERS: dict[str, Layer] = {
    "log_tracer": Layer(internal=()),
    "log_tracer.__main__": Layer(internal=("log_tracer.cli",)),
    "log_tracer.models": Layer(internal=CORE),
    "log_tracer.config": Layer(internal=CORE),
    "log_tracer.errors": Layer(internal=CORE),
    "log_tracer.ingestion": Layer(internal=(*CORE, "log_tracer.ingestion")),
    "log_tracer.analysis": Layer(internal=(*CORE, "log_tracer.analysis")),
    "log_tracer.reporting": Layer(internal=(*CORE, "log_tracer.reporting")),
    # Rich is allowed only where text meets the terminal: here and in the CLI.
    "log_tracer.reporting.terminal": Layer(
        internal=(*CORE, "log_tracer.reporting"), third_party=("rich",)
    ),
    "log_tracer.pipeline": Layer(
        internal=(*CORE, "log_tracer.__version__", "log_tracer.ingestion", "log_tracer.analysis")
    ),
    # The saved state is a report: it is encoded and decoded by json_report.
    "log_tracer.state": Layer(internal=(*CORE, "log_tracer.reporting.json_report")),
    # The CLI is the composition root: it may use every layer, and it is the
    # only module allowed to import Typer.
    "log_tracer.cli": Layer(internal=ANYTHING, third_party=("typer", "rich")),
}


def module_name(path: Path) -> str:
    parts = list(path.relative_to(PACKAGE_DIR.parent).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def all_modules() -> list[str]:
    return sorted(module_name(path) for path in PACKAGE_DIR.rglob("*.py"))


def layer_of(module: str) -> str | None:
    """The most specific layer containing ``module``."""
    candidates = [name for name in LAYERS if module == name or module.startswith(name + ".")]
    candidates = [c for c in candidates if c != "log_tracer" or module == "log_tracer"]
    return max(candidates, key=len, default=None)


def imports_of(module: str) -> Iterator[str]:
    """Fully qualified names imported by ``module``, with relative imports resolved."""
    path = PACKAGE_DIR.parent.joinpath(*module.split("."))
    path = path / "__init__.py" if path.is_dir() else path.with_suffix(".py")
    is_package = path.name == "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                package = module.split(".") if is_package else module.split(".")[:-1]
                anchor = package[: len(package) - (node.level - 1)]
                base = ".".join([*anchor, base] if base else anchor)
            # ``from x import y`` may import a submodule: record ``x.y``.
            yield from (f"{base}.{alias.name}" for alias in node.names)


def is_allowed(name: str, layer: Layer) -> bool:
    top = name.split(".")[0]
    if top != "log_tracer":
        return top in sys.stdlib_module_names or top in layer.third_party
    return any(name == allowed or name.startswith(allowed + ".") for allowed in layer.internal)


def test_package_has_modules() -> None:
    assert "log_tracer.cli" in all_modules()


@pytest.mark.parametrize("module", all_modules())
def test_every_module_belongs_to_a_layer(module: str) -> None:
    assert layer_of(module) is not None, f"{module} is not assigned to a layer in LAYERS"


@pytest.mark.parametrize("module", all_modules())
def test_imports_respect_the_layer_rules(module: str) -> None:
    layer_name = layer_of(module)
    assert layer_name is not None
    violations = [name for name in imports_of(module) if not is_allowed(name, LAYERS[layer_name])]
    assert violations == [], f"{module} ({layer_name} layer) must not import {violations}"


@pytest.mark.parametrize(
    ("name", "layer", "allowed"),
    [
        ("log_tracer.models.Event", LAYERS["log_tracer.analysis"], True),
        ("log_tracer.ingestion.fields", LAYERS["log_tracer.analysis"], False),
        ("log_tracer.cli", LAYERS["log_tracer.pipeline"], False),
        ("typer.Typer", LAYERS["log_tracer.analysis"], False),
        ("typer", LAYERS["log_tracer.cli"], True),
        ("rich.console", LAYERS["log_tracer.models"], False),
        ("rich.console", LAYERS["log_tracer.reporting"], False),
        ("rich.table", LAYERS["log_tracer.reporting.terminal"], True),
        ("log_tracer.reporting.terminal", LAYERS["log_tracer.state"], False),
        ("datetime.UTC", LAYERS["log_tracer.models"], True),
    ],
)
def test_rule_checker_itself(name: str, layer: Layer, allowed: bool) -> None:
    assert is_allowed(name, layer) is allowed

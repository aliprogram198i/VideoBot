import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _literal_imports(source):
    tree = ast.parse(source)
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "import_module" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    modules.add(arg.value)
    return modules


def test_entrypoint_dynamic_imports_are_packaged():
    source = (REPO_ROOT / "entrypoint.py").read_text(encoding="utf-8")
    missing = []
    for module in sorted(_literal_imports(source)):
        parts = module.split(".")
        module_file = REPO_ROOT.joinpath(*parts).with_suffix(".py")
        package_init = REPO_ROOT.joinpath(*parts, "__init__.py")
        if not module_file.is_file() and not package_init.is_file():
            missing.append(module)
    assert not missing, f"entrypoint imports missing source modules: {missing}"


def test_dockerfile_copies_runtime_source_roots():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    required = [
        "bot.py",
        "data_layer.py",
        "entrypoint.py",
        "downloader",
        "jobs",
        "security",
        "plugins",
        "telegram_layer",
    ]
    missing = [item for item in required if f"COPY --chown=videobot:videobot {item}" not in dockerfile]
    assert not missing, f"Dockerfile omits runtime source roots: {missing}"


def test_docker_runtime_entrypoint_is_not_build_time_source_mutated():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "sed -i" not in dockerfile
    assert "open('bot.py'" not in dockerfile
    assert 'open("bot.py"' not in dockerfile

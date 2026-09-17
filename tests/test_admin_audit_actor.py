from pathlib import Path
import ast


SOURCE = Path(__file__).resolve().parents[1] / "plugins" / "admin_control_center.py"


def _audit_calls(tree):
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "audit":
            calls.append(node)
    return calls


def test_admin_audit_uses_effective_actor_for_control_center_actions():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    helper_names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_actor_id" in helper_names

    calls = _audit_calls(tree)
    assert len(calls) == 4
    for call in calls:
        assert len(call.args) >= 2
        actor = call.args[1]
        assert isinstance(actor, ast.Call)
        assert isinstance(actor.func, ast.Name)
        assert actor.func.id == "_actor_id"


def test_actor_helper_prefers_effective_user_and_has_owner_fallback():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    helper = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_actor_id"
    )
    source = ast.get_source_segment(SOURCE.read_text(encoding="utf-8"), helper)
    assert "update.effective_user" in source
    assert "owner_id" in source

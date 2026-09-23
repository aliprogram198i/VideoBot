from plugins.resolver_monitor import render_resolver_monitor


def test_render_resolver_monitor_uses_outcome_fields():
    class _Conn:
        def execute(self, *args):
            class _Cursor:
                def fetchall(self):
                    return []
            return _Cursor()
        def close(self):
            pass
    text = render_resolver_monitor(lambda: _Conn(), days=1)
    assert "Platform / Resolver Monitor" in text
    assert "لا توجد بيانات Resolver" in text

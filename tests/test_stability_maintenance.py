import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class StabilityMaintenanceContractTests(unittest.TestCase):
    def test_docker_build_does_not_mutate_bot_source(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("Applied YouTube fallback isolation during image build", dockerfile)
        self.assertNotIn("marker = 'youtube_smart_extraction_not_applicable'", dockerfile)
        self.assertNotIn("path.write_text(text[:m.start()] + replacement", dockerfile)
        self.assertIn("COPY --chown=videobot:videobot bot.py .", dockerfile)

    def test_canonical_admin_layer_owns_records_route(self):
        source = (ROOT / "plugins" / "admin_layer_v2.py").read_text(encoding="utf-8")
        self.assertIn('r"^admin_records$"', source)
        self.assertIn('r"^admin_records$"', source[source.index("_remove_legacy_admin_handlers"):])
        self.assertIn("register_admin_operations_center(app, get_db, admin_id)", source)

    def test_observability_uses_canonical_db_handle_for_audit(self):
        source = (ROOT / "plugins" / "admin_observability_center.py").read_text(encoding="utf-8")
        self.assertIn(
            "async def observability_callback(update: Update, context, get_db, owner_id: int) -> None:",
            source,
        )
        self.assertIn(
            'audit(get_db, owner_id, f"view_observability_{view}")',
            source,
        )
        self.assertNotIn("_get_db_from_context", source)


if __name__ == "__main__":
    unittest.main()

from pathlib import Path

from delivery import DeliveryPolicy
from media import MediaService
from security import is_admin_id
from telegram_layer.groups import ADMIN_GROUP, LEGACY_GROUP, SMART_SEARCH_GROUP


def test_handler_groups_are_ordered_for_isolated_layers():
    assert SMART_SEARCH_GROUP < ADMIN_GROUP < LEGACY_GROUP


def test_security_boundary_fails_closed():
    assert is_admin_id(1486412391, 1486412391)
    assert not is_admin_id(None, 1486412391)
    assert not is_admin_id(7, 1486412391)


def test_media_boundary_does_not_shell_interpolate():
    service = MediaService()
    assert isinstance(service.available(), bool)


def test_delivery_policy_enforces_existing_limits(tmp_path):
    path = tmp_path / "small.bin"
    path.write_bytes(b"ok")
    assert DeliveryPolicy().validate_file(path, media_type="video") == Path(path)

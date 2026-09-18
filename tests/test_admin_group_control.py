from plugins.admin_group_control import REQUIRED_RIGHTS, _status_label


def test_required_rights_are_bounded():
    assert set(REQUIRED_RIGHTS) == {
        "can_manage_chat",
        "can_delete_messages",
        "can_restrict_members",
        "can_invite_users",
        "can_pin_messages",
        "can_manage_topics",
        "can_change_info",
        "can_promote_members",
    }


def test_status_label():
    assert _status_label("administrator", 1) == "🟢 مشرف"
    assert _status_label("member", 0) == "🟡 عضو"
    assert _status_label("left", 0) == "🔴 غير موجود"

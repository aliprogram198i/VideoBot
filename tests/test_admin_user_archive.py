from plugins.admin_user_links import _ARCHIVE_PAGE_RE, _LINK_PAGE_RE


def test_admin_user_archive_callback_is_canonical_and_paginated():
    match = _ARCHIVE_PAGE_RE.fullmatch("admin_user_archive_12345_10")
    assert match
    assert match.group(1) == "12345"
    assert match.group(2) == "10"


def test_existing_user_links_callback_remains_compatible():
    match = _LINK_PAGE_RE.fullmatch("admin_user_links_12345_0")
    assert match
    assert match.group(1) == "12345"
    assert match.group(2) == "0"

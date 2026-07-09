"""Tests for inbound email actionable/notification classification."""

from app.channels.email_classifier import is_actionable_email


def test_genuine_human_email_is_actionable():
    assert is_actionable_email({}, "Alice <alice@example.com>") is True


def test_github_notification_by_sender_is_not_actionable():
    # The exact pattern that created the 3 phantom task folders.
    headers = {"X-GitHub-Reason": "author", "List-Id": "activepieces/activepieces"}
    assert is_actionable_email(headers, "kishanprmr <notifications@github.com>") is False


def test_auto_submitted_is_not_actionable():
    assert is_actionable_email({"Auto-Submitted": "auto-generated"}, "bot@svc.io") is False


def test_auto_submitted_no_is_actionable():
    # RFC 3834: "no" explicitly means a human sent it.
    assert is_actionable_email({"Auto-Submitted": "no"}, "human@example.com") is True


def test_bulk_precedence_is_not_actionable():
    assert is_actionable_email({"Precedence": "bulk"}, "news@list.io") is False


def test_list_precedence_is_not_actionable():
    assert is_actionable_email({"Precedence": "list"}, "x@list.io") is False


def test_list_id_from_notification_domain_is_not_actionable():
    headers = {"List-Id": "<repo.github.com>"}
    assert is_actionable_email(headers, "someone <noreply@github.com>") is False


def test_list_id_from_normal_domain_stays_actionable():
    # A mailing list from a non-notification domain should not be auto-skipped
    # by List-Id alone (only the combination triggers).
    headers = {"List-Id": "<team.example.com>"}
    assert is_actionable_email(headers, "lead@example.com") is True


def test_github_sender_header_is_not_actionable():
    assert is_actionable_email({"X-GitHub-Sender": "octocat"}, "x@github.com") is False


def test_addr_extraction_handles_display_name():
    assert is_actionable_email({}, "Bob Smith <bob@corp.com>") is True

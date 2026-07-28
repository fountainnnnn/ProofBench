"""A session earns its name from the exchange, once, and never at the chat's cost.

The sidebar used to list truncated opening messages. These tests cover the
server half of the replacement: which turn is allowed to rename a session,
what it names it from, and what happens when naming fails.
"""
from __future__ import annotations

import pytest

from server import runs
import server.main as main_module

from server.test_backend_hardening import (  # noqa: F401  (client fixture)
    client, create_session, headers,
)


class _Identity:
    tenant_id = "tenant-a"


def test_only_a_session_that_has_never_answered_is_unnamed():
    assert main_module._is_unnamed({"messages": [{"role": "user", "text": "hi"}]})
    assert main_module._is_unnamed({})
    assert not main_module._is_unnamed(
        {"messages": [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}]})


def test_the_first_exchange_renames_the_session(client, monkeypatch):
    session_id = create_session(client)
    runs.add_message(session_id, "user", "which service for a RAG chatbot over SharePoint")
    runs.add_message(session_id, "assistant", "Azure AI Search and AWS Bedrock are the options.")

    seen = {}

    def _fake(message, reply, *, env=None, complete=None):
        seen["message"] = message
        seen["reply"] = reply
        return "RAG Platforms for SharePoint"

    monkeypatch.setattr(main_module.session_title, "summarize_title", _fake)
    main_module._retitle(session_id, _Identity(), "which service for a RAG chatbot", None)

    assert runs.get_session(session_id, "tenant-a")["title"] == "RAG Platforms for SharePoint"
    # It titles from the exchange, not from the question alone.
    assert "Azure AI Search" in seen["reply"]


def test_a_failed_rename_leaves_the_session_usable(client, monkeypatch):
    session_id = create_session(client)
    before = runs.get_session(session_id, "tenant-a")["title"]

    def _boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(main_module.session_title, "summarize_title", _boom)
    # No exception escapes: titling must never fail the turn that produced it.
    main_module._retitle(session_id, _Identity(), "compare OCR tools", None)

    assert runs.get_session(session_id, "tenant-a")["title"] == before


def test_a_new_chat_session_starts_with_the_opening_line_as_its_label(client):
    """Before the turn answers, the sidebar still needs something to show."""
    chat = client.post("/api/chat", headers=headers("token-a"),
                       json={"message": "compare OCR tools for scanned invoices"})
    assert chat.status_code == 200

    session = runs.get_session(chat.json()["session_id"], "tenant-a")
    assert session["title"] == "compare OCR tools for scanned invoices"

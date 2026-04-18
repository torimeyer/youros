"""Tests for reply-thread context isolation in the chat router.

Covers:
1. build_thread_context extracts only the root + thread messages.
2. Two independent threads stay isolated from each other.
3. Thread with no matching messages falls back gracefully (returns []).
4. Root messages without thread_id are excluded from another thread's context.
"""

from typing import Optional

import pytest

from routers.chat import build_thread_context


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(id: str, role: str, content: str, thread_id: Optional[str] = None) -> dict:
    m: dict = {"id": id, "role": role, "content": content}
    if thread_id is not None:
        m["thread_id"] = thread_id
    return m


# ---------------------------------------------------------------------------
# build_thread_context unit tests
# ---------------------------------------------------------------------------


class TestBuildThreadContext:
    def test_returns_root_message_by_id(self):
        root = _msg("root-1", "user", "What is the capital of France?")
        other = _msg("root-2", "assistant", "Some unrelated answer")
        messages = [root, other]
        result = build_thread_context(messages, "root-1")
        assert root in result
        assert other not in result

    def test_returns_thread_children(self):
        root = _msg("root-1", "user", "Tell me about Python.")
        child1 = _msg("c1", "assistant", "Python is a language.", thread_id="root-1")
        child2 = _msg("c2", "user", "What about type hints?", thread_id="root-1")
        child3 = _msg("c3", "assistant", "Type hints add static typing.", thread_id="root-1")
        unrelated = _msg("u1", "user", "Different topic.")
        messages = [root, child1, child2, child3, unrelated]
        result = build_thread_context(messages, "root-1")
        assert root in result
        assert child1 in result
        assert child2 in result
        assert child3 in result
        assert unrelated not in result

    def test_two_threads_stay_isolated(self):
        root_a = _msg("A", "user", "Thread A root.")
        child_a = _msg("A1", "assistant", "Thread A reply.", thread_id="A")
        root_b = _msg("B", "user", "Thread B root.")
        child_b = _msg("B1", "assistant", "Thread B reply.", thread_id="B")
        messages = [root_a, child_a, root_b, child_b]

        ctx_a = build_thread_context(messages, "A")
        assert root_a in ctx_a
        assert child_a in ctx_a
        assert root_b not in ctx_a
        assert child_b not in ctx_a

        ctx_b = build_thread_context(messages, "B")
        assert root_b in ctx_b
        assert child_b in ctx_b
        assert root_a not in ctx_b
        assert child_a not in ctx_b

    def test_returns_empty_for_unknown_thread_id(self):
        messages = [_msg("x", "user", "Hello")]
        result = build_thread_context(messages, "nonexistent-id")
        assert result == []

    def test_preserves_order(self):
        root = _msg("root-1", "user", "Start")
        c1 = _msg("c1", "assistant", "A", thread_id="root-1")
        c2 = _msg("c2", "user", "B", thread_id="root-1")
        c3 = _msg("c3", "assistant", "C", thread_id="root-1")
        messages = [root, c1, c2, c3]
        result = build_thread_context(messages, "root-1")
        assert result == [root, c1, c2, c3]

    def test_ignores_messages_with_different_thread_id(self):
        root = _msg("root-1", "user", "Root")
        child = _msg("c1", "assistant", "Child", thread_id="root-1")
        wrong_thread = _msg("w1", "user", "Wrong thread", thread_id="other-root")
        messages = [root, child, wrong_thread]
        result = build_thread_context(messages, "root-1")
        assert wrong_thread not in result

    def test_root_message_with_thread_id_is_not_mistaken_as_root(self):
        # A message that has both id=="root-1" AND thread_id set means it
        # is actually a child of some other thread, not the root of root-1.
        # It should not be returned as the root anchor.
        not_a_root = _msg("root-1", "user", "I am actually a child")
        not_a_root["thread_id"] = "other"
        child = _msg("c1", "assistant", "Reply", thread_id="root-1")
        messages = [not_a_root, child]
        result = build_thread_context(messages, "root-1")
        # not_a_root should NOT appear because its thread_id != "root-1"
        # AND it fails the root check (has a thread_id set).
        assert not_a_root not in result
        assert child in result

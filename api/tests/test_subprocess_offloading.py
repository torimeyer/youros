"""RED test for →1416: async subprocess offloading.

Asserts that the sync subprocess helpers in upgrade_check and provider_detection
are coroutine functions. This test FAILS on the current codebase (they are sync).
After the fix they become async and the test passes.
"""
import inspect
import pytest


def test_check_myos_is_coroutine():
    from services.upgrade_check import check_myos
    assert inspect.iscoroutinefunction(check_myos), (
        "check_myos must be async def — it calls subprocess.run synchronously "
        "and blocks the event loop from async request handlers"
    )


def test_detect_vertex_ai_is_coroutine():
    from services.provider_detection import detect_vertex_ai
    assert inspect.iscoroutinefunction(detect_vertex_ai), (
        "detect_vertex_ai must be async def — it calls subprocess.run synchronously"
    )


def test_resolve_gcloud_default_project_is_coroutine():
    from services.provider_detection import _resolve_gcloud_default_project
    assert inspect.iscoroutinefunction(_resolve_gcloud_default_project), (
        "_resolve_gcloud_default_project must be async def — it calls subprocess.run synchronously"
    )


def test_detect_bedrock_is_coroutine():
    from services.provider_detection import detect_bedrock
    assert inspect.iscoroutinefunction(detect_bedrock), (
        "detect_bedrock must be async def — it calls subprocess.run synchronously"
    )

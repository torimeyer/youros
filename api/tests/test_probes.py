import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from services.probes import probe_claude_api, get_last_probe_result


@pytest.mark.asyncio
async def test_probe_claude_api_success(monkeypatch):
    """Test probe succeeds when Claude responds with 'ok' within timeout."""
    import anthropic
    
    # Mock the Anthropic client
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="ok")]
    
    mock_client = MagicMock()
    mock_client.messages.create = MagicMock(return_value=mock_message)
    
    with patch('anthropic.Anthropic', return_value=mock_client):
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'}):
            result = await probe_claude_api()
            
            assert result['status'] == 'pass'
            assert result['error'] is None
            assert 'timestamp' in result
            assert 'duration_ms' in result


@pytest.mark.asyncio
async def test_probe_claude_api_missing_ok(monkeypatch):
    """Test probe fails when response doesn't contain 'ok'."""
    import anthropic
    
    # Mock the Anthropic client with bad response
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text="hello world")]
    
    mock_client = MagicMock()
    mock_client.messages.create = MagicMock(return_value=mock_message)
    
    with patch('anthropic.Anthropic', return_value=mock_client):
        with patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'}):
            result = await probe_claude_api()
            
            assert result['status'] == 'fail'
            assert "Response missing 'ok'" in result['error']


@pytest.mark.asyncio
async def test_probe_claude_api_no_key():
    """Test probe fails when API key is missing."""
    import os
    
    env = os.environ.copy()
    if 'ANTHROPIC_API_KEY' in env:
        del env['ANTHROPIC_API_KEY']
    
    with patch.dict('os.environ', env, clear=True):
        result = await probe_claude_api()
        
        assert result['status'] == 'fail'
        assert "ANTHROPIC_API_KEY not set" in result['error']

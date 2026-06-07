import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from services.text_bridge import classify_and_dispatch, text_bridge
from services.settings_store import settings_store

@pytest.mark.asyncio
async def test_classify_and_dispatch_task_creation():
    # Mock AI client to return a tool_use block for create_task
    mock_resp = MagicMock()
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.name = "create_task"
    tool_use.input = {"title": "Call the dentist", "description": "Remind me to call at 3pm"}
    mock_resp.content = [tool_use]
    
    mock_client = AsyncMock()
    mock_client.messages.create.return_value = mock_resp
    
    with patch("services.text_bridge.get_ai_client", AsyncMock(return_value=mock_client)), \
         patch("services.text_bridge.resolve_ai_backend", AsyncMock(return_value={"provider": "anthropic"})), \
         patch("services.tool_executor.execute_tool", AsyncMock()) as mock_execute:
        
        reply = await classify_and_dispatch("remind me to call the dentist", "vmeyer")
        
        assert "Task created" in reply
        assert "Call the dentist" in reply
        mock_execute.assert_called_once_with("create_task", tool_use.input)

@pytest.mark.asyncio
async def test_classify_and_dispatch_chat():
    # Mock AI client to return plain text (chat)
    mock_resp = MagicMock()
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "I will remember that."
    mock_resp.content = [text_block]
    
    mock_client = AsyncMock()
    mock_client.messages.create.return_value = mock_resp
    
    with patch("services.text_bridge.get_ai_client", AsyncMock(return_value=mock_client)), \
         patch("services.text_bridge.resolve_ai_backend", AsyncMock(return_value={"provider": "anthropic"})):
        reply = await classify_and_dispatch("what's running?", "vmeyer")
        assert reply == "I will remember that."

@pytest.mark.asyncio
async def test_telegram_polling_integration():
    # Mock TelegramPoller
    mock_poller = MagicMock()
    
    with patch("services.telegram_channel.TelegramPoller", return_value=mock_poller):
        # Configure telegram
        settings_store.update({
            "telegram": {"token": "test_token", "chat_id": "12345"},
            "text_bridge": {"enabled": True}
        })
        
        text_bridge.start()
        
        assert text_bridge._telegram_poller is not None
        mock_poller.start.assert_called_once()

@pytest.mark.asyncio
async def test_handle_inbound_telegram_message():
    msg = {
        "service": "Telegram",
        "id": "1",
        "chat_id": "12345",
        "sender": "tori_user",
        "text": "hello",
        "date": 123456789.0
    }
    
    with patch("services.text_bridge.is_trusted_sender", return_value=True), \
         patch("services.text_bridge.classify_and_dispatch", AsyncMock(return_value="Hi Tori!")) as mock_classify, \
         patch("services.text_bridge.append_chat_interaction") as mock_append:
        
        text_bridge._telegram_poller = AsyncMock()
        await text_bridge.handle_inbound_message(msg)
        
        mock_classify.assert_called_once_with("hello", "tori_user")
        assert mock_append.call_count == 2
        text_bridge._telegram_poller.send_message.assert_called_once_with("12345", "Hi Tori!")

@pytest.mark.asyncio
async def test_classify_and_dispatch_list_tasks():
    # Mock AI client to return a tool_use block for list_tasks
    mock_resp = MagicMock()
    tool_use = MagicMock()
    tool_use.type = "tool_use"
    tool_use.name = "list_tasks"
    tool_use.input = {}
    mock_resp.content = [tool_use]
    
    mock_client = AsyncMock()
    mock_client.messages.create.return_value = mock_resp
    
    with patch("services.text_bridge.get_ai_client", AsyncMock(return_value=mock_client)), \
         patch("services.text_bridge.resolve_ai_backend", AsyncMock(return_value={"provider": "anthropic"})), \
         patch("services.tool_executor.execute_tool", AsyncMock(return_value="→2163 [P2|open] Spec promoted...")) as mock_execute:
        
        reply = await classify_and_dispatch("view task list", "vmeyer")
        
        assert "→2163" in reply
        mock_execute.assert_called_once_with("list_tasks", {})

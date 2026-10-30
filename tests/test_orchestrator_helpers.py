from agent.orchestrator import Orchestrator


def test_attach_images_converts_last_user_message():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
    Orchestrator._attach_images(msgs, ["data:image/png;base64,AAA"])
    parts = msgs[1]["content"]
    assert parts[0] == {"type": "text", "text": "hi"}
    assert parts[1]["image_url"]["url"].startswith("data:image/png")


def test_inject_vault_memory_appends_to_system(monkeypatch):
    from tool.execute.execute_local_memory import vault_context

    monkeypatch.setattr(vault_context, "build_context", lambda: "\nMEM")
    msgs = [{"role": "system", "content": "s"}]
    Orchestrator._inject_vault_memory(msgs)
    assert msgs[0]["content"] == "s\nMEM"
    blocks = [{"role": "system", "content": [{"type": "text", "text": "s", "cache_control": {}}]}]
    Orchestrator._inject_vault_memory(blocks)
    assert blocks[0]["content"][-1]["text"] == "\nMEM"

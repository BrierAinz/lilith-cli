from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from lilith_cli.providers import LLMProviderWrapper


class _MockClient:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.payloads: list[dict] = []
        self.is_closed = False

    async def post(self, path: str, *, json: dict):
        self.payloads.append(json)
        response = httpx.Response(
            200,
            json=self.responses.pop(0),
            request=httpx.Request("POST", f"https://mock.example{path}"),
        )
        return response

    async def aclose(self) -> None:
        self.is_closed = True


def _config():
    profile = SimpleNamespace(
        base_url="https://mock.example/anthropic",
        api_key="test",
        model="MiniMax-M2",
        max_tokens=1024,
        use_responses=False,
    )
    return SimpleNamespace(
        provider="minimax",
        providers={"minimax": profile},
        base_url=profile.base_url,
        api_key="test",
        model=profile.model,
        max_tokens=1024,
        temperature=0.2,
    )


@pytest.mark.asyncio
async def test_anthropic_tools_and_tool_results_round_trip():
    client = _MockClient(
        [
            {
                "model": "MiniMax-M2",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "file_write",
                        "input": {"path": "saludo.txt", "content": "Yggdrasil vive."},
                    }
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
            {
                "model": "MiniMax-M2",
                "content": [{"type": "text", "text": "done"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 15, "output_tokens": 2},
            },
        ]
    )
    provider = LLMProviderWrapper(_config())
    provider._client = client
    tools = [
        {
            "type": "function",
            "function": {
                "name": "file_write",
                "description": "Write a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "write it"}]

    first = await provider.complete(messages, tools=tools)
    assert first["finish_reason"] == "tool_calls"
    assert first["tool_calls"][0].name == "file_write"
    assert first["tool_calls"][0].arguments["path"] == "saludo.txt"
    assert client.payloads[0]["tools"] == [
        {
            "name": "file_write",
            "description": "Write a file",
            "input_schema": tools[0]["function"]["parameters"],
        }
    ]

    messages.extend(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "toolu_1",
                        "type": "function",
                        "function": {
                            "name": "file_write",
                            "arguments": '{"path":"saludo.txt","content":"Yggdrasil vive."}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "toolu_1",
                "content": '{"ok":true,"path":"saludo.txt"}',
            },
        ]
    )
    second = await provider.complete(messages, tools=tools)

    assert second["content"] == "done"
    assert client.payloads[1]["messages"][-2] == {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "file_write",
                "input": {"path": "saludo.txt", "content": "Yggdrasil vive."},
            }
        ],
    }
    assert client.payloads[1]["messages"][-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_1",
                "content": '{"ok":true,"path":"saludo.txt"}',
            }
        ],
    }

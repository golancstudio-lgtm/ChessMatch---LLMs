"""
LLM adapter abstraction and implementations.

Defines a common interface for sending prompts to LLMs and receiving responses.
Concrete adapters (ChatGPT, Gemini) implement this interface.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


# --- Adapter IDs (for programmatic selection) ---
CHATGPT_ID = "chatgpt"
GEMINI_ID = "gemini"
CLAUDE_ID = "claude"
MISTRAL_ID = "mistral"
COHERE_ID = "cohere"
LLAMA_GROQ_ID = "llama_groq"
GROK_ID = "grok"


@runtime_checkable
class LLMAdapter(Protocol):
    """
    Protocol for LLM adapters: send prompts, receive text responses.

    Any adapter that implements send_prompt and provides name/id
    can be used by the game loop.
    """

    @property
    def name(self) -> str:
        """Display name for the LLM (e.g. 'ChatGPT 5.2', 'Gemini')."""
        ...

    @property
    def id(self) -> str:
        """Unique identifier for programmatic selection (e.g. 'chatgpt', 'gemini')."""
        ...

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a prompt to the LLM and return the raw text response.

        Args:
            system_prompt: System/instruction message (defines behavior).
            user_prompt: User message (e.g. current board state, move request).

        Returns:
            The LLM's response as a string. May contain extra text;
            the response parser will extract the move.
        """
        ...


class BaseLLMAdapter(ABC):
    """
    Abstract base class for LLM adapters.

    Subclasses must implement send_prompt and set name/id.
    """

    def __init__(self, name: str, adapter_id: str) -> None:
        self._name = name
        self._id = adapter_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def id(self) -> str:
        return self._id

    @abstractmethod
    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        """
        Send a prompt to the LLM and return the raw text response.

        Args:
            system_prompt: System/instruction message.
            user_prompt: User message (board state, move request, etc.).

        Returns:
            The LLM's response as a string.
        """
        pass


# --- Concrete Adapters ---


class ChatGPTAdapter(BaseLLMAdapter):
    """
    Adapter for OpenAI's ChatGPT (GPT-5.2).
    Requires OPENAI_API_KEY environment variable.
    """

    def __init__(
        self,
        name: str = "ChatGPT 5.2",
        adapter_id: str = CHATGPT_ID,
        model: str = "gpt-5.2-chat-latest",
    ) -> None:
        super().__init__(name=name, adapter_id=adapter_id)
        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            from openai import OpenAI

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OPENAI_API_KEY environment variable is not set. "
                    "Set it to use the ChatGPT adapter."
                )
            self._client = OpenAI(api_key=api_key)
        return self._client

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        completion = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = completion.choices[0].message.content
        return content if content else ""


class GeminiAdapter(BaseLLMAdapter):
    """
    Adapter for Google's Gemini.
    Requires GEMINI_API_KEY or GOOGLE_API_KEY environment variable.
    """

    def __init__(
        self,
        name: str = "Gemini",
        adapter_id: str = GEMINI_ID,
        model: str = "gemini-2.5-flash",
    ) -> None:
        super().__init__(name=name, adapter_id=adapter_id)
        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Google GenAI client."""
        if self._client is None:
            from google import genai

            api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get(
                "GOOGLE_API_KEY"
            )
            if not api_key:
                raise ValueError(
                    "GEMINI_API_KEY or GOOGLE_API_KEY environment variable is not set. "
                    "Set one of them to use the Gemini adapter."
                )
            self._client = genai.Client(api_key=api_key)
        return self._client

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        from google.genai import types

        client = self._get_client()
        response = client.models.generate_content(
            model=self._model,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
            ),
        )
        return response.text if response.text else ""


class ClaudeAdapter(BaseLLMAdapter):
    """
    Adapter for Anthropic's Claude.
    Requires ANTHROPIC_API_KEY environment variable.
    """

    def __init__(
        self,
        name: str = "Claude",
        adapter_id: str = CLAUDE_ID,
        model: str = "claude-sonnet-4-5-20250929",
    ) -> None:
        super().__init__(name=name, adapter_id=adapter_id)
        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Anthropic client."""
        if self._client is None:
            from anthropic import Anthropic

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "ANTHROPIC_API_KEY environment variable is not set. "
                    "Set it to use the Claude adapter."
                )
            self._client = Anthropic(api_key=api_key)
        return self._client

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        message = client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        if message.content and len(message.content) > 0:
            block = message.content[0]
            if hasattr(block, "text"):
                return block.text
        return ""


class MistralAdapter(BaseLLMAdapter):
    """
    Adapter for Mistral AI.
    Requires MISTRAL_API_KEY environment variable.
    """

    def __init__(
        self,
        name: str = "Mistral",
        adapter_id: str = MISTRAL_ID,
        model: str = "mistral-large-latest",
    ) -> None:
        super().__init__(name=name, adapter_id=adapter_id)
        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Mistral client."""
        if self._client is None:
            from mistralai import Mistral

            api_key = os.environ.get("MISTRAL_API_KEY")
            if not api_key:
                raise ValueError(
                    "MISTRAL_API_KEY environment variable is not set. "
                    "Set it to use the Mistral adapter."
                )
            self._client = Mistral(api_key=api_key)
        return self._client

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        response = client.chat.complete(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        if response.choices and len(response.choices) > 0:
            msg = response.choices[0].message
            return msg.content if msg.content else ""
        return ""


class CohereAdapter(BaseLLMAdapter):
    """
    Adapter for Cohere Command.
    Requires COHERE_API_KEY environment variable.
    """

    def __init__(
        self,
        name: str = "Cohere",
        adapter_id: str = COHERE_ID,
        model: str = "command-r-plus-08-2024",
    ) -> None:
        super().__init__(name=name, adapter_id=adapter_id)
        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Cohere client."""
        if self._client is None:
            from cohere import ClientV2

            api_key = os.environ.get("COHERE_API_KEY")
            if not api_key:
                raise ValueError(
                    "COHERE_API_KEY environment variable is not set. "
                    "Set it to use the Cohere adapter."
                )
            self._client = ClientV2(api_key=api_key)
        return self._client

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        response = client.chat(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        if response.message and response.message.content:
            for part in response.message.content:
                text = getattr(part, "text", None)
                if text:
                    return text
        return ""


class LlamaGroqAdapter(BaseLLMAdapter):
    """
    Adapter for Meta Llama via Groq (fast inference).
    Requires GROQ_API_KEY environment variable.
    """

    def __init__(
        self,
        name: str = "Llama (Groq)",
        adapter_id: str = LLAMA_GROQ_ID,
        model: str = "llama-3.3-70b-versatile",
    ) -> None:
        super().__init__(name=name, adapter_id=adapter_id)
        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the Groq client."""
        if self._client is None:
            from groq import Groq

            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise ValueError(
                    "GROQ_API_KEY environment variable is not set. "
                    "Set it to use the Llama (Groq) adapter."
                )
            self._client = Groq(api_key=api_key)
        return self._client

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        completion = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = completion.choices[0].message.content
        return content if content else ""


class GrokAdapter(BaseLLMAdapter):
    """
    Adapter for xAI's Grok.
    Requires XAI_API_KEY environment variable.
    Uses OpenAI-compatible API.
    """

    def __init__(
        self,
        name: str = "Grok",
        adapter_id: str = GROK_ID,
        model: str = "grok-2",
    ) -> None:
        super().__init__(name=name, adapter_id=adapter_id)
        self._model = model
        self._client = None

    def _get_client(self):
        """Lazy-initialize the xAI client (OpenAI-compatible)."""
        if self._client is None:
            from openai import OpenAI

            api_key = os.environ.get("XAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "XAI_API_KEY environment variable is not set. "
                    "Set it to use the Grok adapter."
                )
            self._client = OpenAI(
                api_key=api_key,
                base_url="https://api.x.ai/v1",
            )
        return self._client

    def send_prompt(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        completion = client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = completion.choices[0].message.content
        return content if content else ""


# --- Registry for UI selection ---


def get_available_adapters() -> list[LLMAdapter]:
    """Return all available LLM adapters."""
    return [
        ChatGPTAdapter(),
        GeminiAdapter(),
        ClaudeAdapter(),
        MistralAdapter(),
        CohereAdapter(),
        LlamaGroqAdapter(),
        GrokAdapter(),
    ]


def get_adapter_by_id(adapter_id: str) -> LLMAdapter | None:
    """Return the adapter with the given id, or None if not found."""
    for adapter in get_available_adapters():
        if adapter.id == adapter_id:
            return adapter
    return None

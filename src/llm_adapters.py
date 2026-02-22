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


# --- Registry for UI selection ---


def get_available_adapters() -> list[LLMAdapter]:
    """Return all available LLM adapters."""
    return [
        ChatGPTAdapter(),
        GeminiAdapter(),
    ]


def get_adapter_by_id(adapter_id: str) -> LLMAdapter | None:
    """Return the adapter with the given id, or None if not found."""
    for adapter in get_available_adapters():
        if adapter.id == adapter_id:
            return adapter
    return None

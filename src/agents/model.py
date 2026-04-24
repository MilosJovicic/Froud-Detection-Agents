import os

from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider


def qwen_model(temperature: float = 0.0, max_tokens: int = 256) -> OpenAIModel:
    return OpenAIModel(
        model_name=os.getenv("OLLAMA_MODEL", "qwen3:4b"),
        provider=OpenAIProvider(
            base_url=os.getenv("OLLAMA_URL", "http://localhost:11434/v1"),
            api_key="ollama",
        ),
        settings={"temperature": temperature, "max_tokens": max_tokens},
    )

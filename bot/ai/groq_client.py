import asyncio
import logging
from typing import Optional

from groq import AsyncGroq

logger = logging.getLogger(__name__)


class GroqClient:
    """Async wrapper for the official Groq Python SDK."""

    def __init__(self, api_key: str, model_name: str = "llama-3.1-8b-instant"):
        self.client = AsyncGroq(api_key=api_key)
        self.model_name = model_name

    async def chat(
        self,
        user_message: str,
        history: list[dict],
        system_prompt: str,
        temperature: float = 0.75,
        model_name: Optional[str] = None,
        max_retries: int = 3,
        rate_limit_msg: str = "⚠️ I'm currently overwhelmed with messages. Please try again later!",
    ) -> str:
        """Send a chat message via Groq with conversation history."""
        
        # Build the messages array for Groq (OpenAI format)
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add history
        for msg in history:
            role = msg["role"]
            if role == "model":
                role = "assistant"
            content = msg["parts"][0]["text"]
            messages.append({"role": role, "content": content})
            
        # Add the new user message
        messages.append({"role": "user", "content": user_message})

        for attempt in range(max_retries):
            try:
                response = await self.client.chat.completions.create(
                    messages=messages,
                    model=model_name or self.model_name,
                    temperature=temperature,
                    max_tokens=1024,
                )
                return response.choices[0].message.content

            except Exception as e:
                if "rate limit" in str(e).lower() or "429" in str(e) or "400" in str(e) or "decommissioned" in str(e).lower():
                    if attempt == max_retries - 1:
                        logger.warning("Rate limit / Model error exceeded after %d retries: %s", max_retries, e)
                        return rate_limit_msg
                    wait = 2 ** attempt
                    await asyncio.sleep(wait)
                else:
                    logger.error("Unexpected GroqClient.chat error: %s", e)
                    return "⚠️ Something went wrong processing your message."

        return "⚠️ Failed after multiple retries."

    def update_model(self, model_name: str):
        self.model_name = model_name
        logger.info("Groq model updated to: %s", model_name)

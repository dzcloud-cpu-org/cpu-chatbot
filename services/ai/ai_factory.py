# ai_factory.py
import os
from dotenv import load_dotenv

load_dotenv()

AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").lower()
print("AI_PROVIDER =", AI_PROVIDER)

if AI_PROVIDER == "gemini":

    from .gemini_service import (
        generate_chat_response,
        generate_structured_response,
    )

elif AI_PROVIDER == "openai":

    from .openai_service import (
        generate_chat_response,
        generate_structured_response,
    )

elif AI_PROVIDER == "claude":

    from .claude_service import (
        generate_chat_response,
        generate_structured_response,
    )

else:

    raise ValueError(
        f"지원하지 않는 AI_PROVIDER : {AI_PROVIDER}"
    )
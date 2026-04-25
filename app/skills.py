from langchain.agents.middleware import ModelRequest, ModelResponse, AgentMiddleware
from langchain.messages import SystemMessage
from typing import Callable, Awaitable
import anyio
from langchain.tools import tool
from loguru import logger


@tool
async def load_skill(skill_path: str) -> str:
    """Load the full content of a skill into the agent's context.

    Use this when you need detailed information about how to handle a specific
    type of request. This will provide you with comprehensive instructions,
    policies, and guidelines for the skill area.

    Args:
        skill_path: The path to the skill file to load (e.g., "skills/expense_reporting.md", "skills/travel_booking.md")
    """

    file_path = anyio.Path(skill_path)
    content = await file_path.read_text(encoding="utf-8")
    return content


async def load_skill_index():
    try:
        file_path = anyio.Path("./skills/SKILLS.md")
        content = await file_path.read_text(encoding="utf-8")
        return content
    except Exception as e:
        logger.error(f"Error loading skill index: {e}")
        return "No skills available at the moment."

class SkillMiddleware(AgentMiddleware):
    """Middleware that injects skill descriptions into the system prompt."""

    # Register the load_skill tool as a class variable
    tools = [load_skill]

    def __init__(self):
        """Initialize and generate the skills prompt from SKILLS."""
        self.skills_prompt = ""

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Async: Inject skill descriptions into system prompt."""
        # Build the skills addendum

        self.skills_prompt = await load_skill_index()
        skills_addendum = (
            f"\n\n## Available Skills\n\n{self.skills_prompt}\n\n"
            "Use the load_skill tool when you need detailed information "
            "about handling a specific type of request."
        )

        # Append to system message content blocks
        new_content = list(request.system_message.content_blocks) + [
            {"type": "text", "text": skills_addendum}
        ]
        new_system_message = SystemMessage(content=new_content)
        modified_request = request.override(system_message=new_system_message)

        # Ojo aquí: se debe hacer 'await' al handler
        return await handler(modified_request)



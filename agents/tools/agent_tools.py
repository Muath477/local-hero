"""Lets one agent delegate a sub-task to another agent role, as a tool call.

This is what makes the "council" role (see agent_manager.SYSTEM_PROMPTS)
an actual multi-agent collaborator instead of a single model: it gets these
functions in its tool list exactly like web_search or read_uploaded_file,
so delegating to a specialist is just another tool call the model already
knows how to make — no new protocol, no change to the tool-calling loop.

Still respects the one-model-at-a-time hardware constraint: a delegate call
blocks until the sub-agent's Ollama call returns (same sequential handoff
as everything else in agent_manager.py), it just happens *during* the
calling agent's own tool loop instead of before it.

The manager instance is injected at startup (AgentManager.__init__) rather
than imported here, so this module never imports orchestrator.agent_manager
— that import would run straight back into tools.registry, which is what
registers this module's own tools in the first place.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import tool

if TYPE_CHECKING:
    from orchestrator.agent_manager import AgentManager

# Only the council may delegate; every other role's tool pool excludes these.
DELEGATE_TOOLS = ("ask_writer_agent", "ask_coder_agent", "ask_researcher_agent")

_manager: "AgentManager | None" = None


def set_manager(manager: "AgentManager") -> None:
    global _manager
    _manager = manager


def _delegate(role: str, task: str) -> str:
    if _manager is None:
        return f"Error: agent delegation isn't wired up yet (no manager registered for '{role}')."
    try:
        result = _manager.run(role, task)
    except RuntimeError as e:  # e.g. role disabled on this hardware tier
        return f"Error: '{role}' agent unavailable — {e}"
    return result["content"]


@tool(
    "Delegate a sub-task to the general writing/reasoning agent — summarizing, "
    "drafting text, explaining a concept in plain language. Use for the "
    "non-code, non-file parts of a larger request."
)
def ask_writer_agent(task: str) -> str:
    return _delegate("writer_general", task)


@tool(
    "Delegate a sub-task to the coding agent — writing, fixing or explaining "
    "actual code. Use for the code parts of a larger request instead of "
    "writing code yourself."
)
def ask_coder_agent(task: str) -> str:
    return _delegate("coder", task)


@tool(
    "Delegate a sub-task to the file/document research agent, which answers "
    "strictly from whatever the user has already uploaded. Use only when the "
    "request is clearly about an uploaded file's contents."
)
def ask_researcher_agent(task: str) -> str:
    return _delegate("researcher_rag", task)

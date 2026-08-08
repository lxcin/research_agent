"""CLI entry point for PaperPilot research agent."""
import os
import sys

import click

from research_agent.agent import chat, AgentState
from research_agent.llm import LiteLLMProvider
from research_agent.config import get_api_key, get_model_config


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--model", default=None, help="LLM model name (e.g. deepseek/deepseek-chat)")
@click.option("--api-key", default=None, help="API key for the LLM provider")
@click.option("--api-base", default=None, help="Custom API base URL")
def main(model: str | None, api_key: str | None, api_base: str | None):
    """PaperPilot - Research Agent CLI."""
    key = api_key or get_api_key()
    if not key:
        click.echo("Error: No API key configured. Use --api-key or set ANTHROPIC_API_KEY.", err=True)
        sys.exit(1)

    model_name = model or get_model_config().get("name", "deepseek/deepseek-chat")
    click.echo(f"PaperPilot CLI (model: {model_name})")
    click.echo("Type /exit to quit, /new to start a new conversation.\n")

    state = AgentState()
    while True:
        try:
            user_input = click.prompt("You", prompt_suffix="> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo("\nGoodbye!")
            break

        if user_input.lower() in ("/exit", "/quit", ":q"):
            click.echo("Goodbye!")
            break
        if user_input.lower() in ("/new", "/reset"):
            state = AgentState()
            click.echo("Starting a new conversation.\n")
            continue
        if not user_input:
            continue

        click.echo()
        llm = LiteLLMProvider(model=model_name, api_key=key, api_base=api_base)
        from research_agent.agent import run_agent
        result = run_agent(user_input, llm, state)
        click.echo(result.final_response or "(no response)")
        click.echo()

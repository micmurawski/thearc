"""Configuration selection for portable evidence examples."""

import argparse
from pathlib import Path

from thearc import MetaAgent


def project_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project", type=Path, default=Path("tests/fastapi"))
    parser.add_argument("--implementation", choices=["codex", "claude", "antigravity", "pi"], default="codex")
    parser.add_argument("--skill", default="graphify", help="Evaluate this skill and matching context/hooks/resources")


def load_config(args) -> MetaAgent:
    agent = MetaAgent.from_project(args.implementation, args.project)
    if args.skill not in agent.skills:
        raise ValueError(f"Skill {args.skill!r} was not found in {args.project}")
    agent.skills = {args.skill: agent.skills[args.skill]}
    agent.hooks = {
        name: hook for name, hook in agent.hooks.items() if args.skill.lower() in hook.model_dump_json().lower()
    }
    agent.context = {name: doc for name, doc in agent.context.items() if args.skill.lower() in doc.content.lower()}
    agent.resources = {
        name: resource for name, resource in agent.resources.items() if args.skill.lower() in resource.content.lower()
    }
    agent.mcps = {}
    return agent

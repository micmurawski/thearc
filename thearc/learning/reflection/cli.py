"""CLI for prompt reflection and immutable-history handoff."""

import importlib
import json
from pathlib import Path

import click

from thearc.learning.reflection.runtime import ReflectionError
from thearc.models import MetaAgent
from thearc.resources import Reflection, Reflector, SessionStore

IMPLEMENTATIONS = ["codex", "claude", "antigravity", "custom"]


def load_agent(path):
    if path is None:
        return None
    return MetaAgent.from_workspace(path) if path.is_dir() else MetaAgent.model_validate_json(path.read_text())


def runner_for(implementation, address):
    """Validate the address now, import trusted application code on execution."""
    if implementation != "custom":
        if address:
            raise ValueError("--runner requires --implementation custom")
        return None
    module, separator, name = (address or "").partition(":")
    if not separator or not name.isidentifier() or not all(part.isidentifier() for part in module.split(".")):
        raise ValueError("Custom execution requires --runner package.module:function")

    def run(prompt, schema, config):
        callback = getattr(importlib.import_module(module), name)
        if not callable(callback):
            raise TypeError("Runner must be callable")
        return callback(prompt, schema, config)
    return run


def common_options(command):
    options = [
        click.option("--implementation", required=True, type=click.Choice(IMPLEMENTATIONS)),
        click.option("--model", required=True),
        click.option("--prompt-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path),
                     help="Complete operator prompt; include formatted evidence for prompt reflection."),
        click.option("--agent", "agent_path", type=click.Path(exists=True, path_type=Path)),
        click.option("--agent-mode", type=click.Choice(["reference", "active"]), default="reference"),
        click.option("--schema", "schema_path", type=click.Path(exists=True, dir_okay=False, path_type=Path)),
        click.option("--validate-resources", is_flag=True),
        click.option("--runner", help="Custom implementation: trusted package.module:function."),
        click.option("--timeout", type=click.FloatRange(min=1), default=180),
        click.option("--output", required=True, type=click.Path(path_type=Path)),
        click.option("--execute", is_flag=True, help="Run inference; otherwise save a preview."),
    ]
    for option in reversed(options):
        command = option(command)
    return command


def execute_request(*, implementation, model, prompt_file, agent_path, agent_mode, schema_path,
                    validate_resources, runner, timeout, output, execute, history=None):
    inputs = [prompt_file, agent_path, schema_path]
    destination = output.resolve()
    if any(part.is_symlink() for part in (output.absolute(), *output.absolute().parents)):
        raise ValueError("Output must not contain symlinks")
    if any(p and (p.resolve() == destination or (p.is_dir() and p.resolve() in destination.parents)) for p in inputs):
        raise ValueError("Output must be outside input paths")
    reflector = Reflector(implementation=implementation, model=model,
                          runner=runner_for(implementation, runner), timeout_seconds=timeout, artifact_dir=output)
    prompt = prompt_file.read_text(encoding="utf-8")
    options = {"agent": load_agent(agent_path), "agent_mode": agent_mode,
               "output_schema": json.loads(schema_path.read_text()) if schema_path else None,
               "validate_resources": validate_resources}
    prepared = reflector.prepare(prompt, **options)
    if history is not None:
        prepared["history"] = {"artifact_id": (history.session if hasattr(history, "session") else history).artifact_id,
                               "event_ids": [e.id for e in history.selected_events]}
    output.mkdir(parents=True, exist_ok=False)
    preview = {key: value for key, value in prepared.items() if key != "agent"}
    (output / "preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2) + "\n")
    if execute:
        result = (reflector.reflect(prompt, **options) if history is None
                  else reflector.handoff(prompt, history, **options))
        click.echo(json.dumps({"id": result.id, "text": result.text}, ensure_ascii=False, indent=2))
    else:
        click.echo(json.dumps({"status": "prepared", "preview": str(output / "preview.json")}))


@click.group()
def reflection():
    """Reflect on an operator prompt or continue captured session history."""


@reflection.command("run")
@common_options
def run_command(**options):
    """Reflect on PROMPT + operator-formatted EVIDENCE, with an optional output schema."""
    try:
        execute_request(**options)
    except (ValueError, TypeError, OSError, KeyError, ImportError, AttributeError, ReflectionError) as exc:
        raise click.ClickException(str(exc)) from exc


@reflection.command("handoff")
@common_options
@click.option("--index", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--session-id", required=True, help="Canonical indexed session ID.")
@click.option("--trajectory", type=click.IntRange(min=0), help="Zero-based trajectory index.")
@click.option("--through", is_flag=True, help="Include the session prefix through the selected trajectory.")
def handoff_command(index, session_id, trajectory, through, **options):
    """Append the reflection prompt to a captured session or trajectory."""
    try:
        if through and trajectory is None:
            raise ValueError("--through requires --trajectory")
        if options['output'].resolve() == index.resolve():
            raise ValueError("Output must be outside the index")
        with SessionStore(index) as store:
            session = store.get(session_id)
            history = session
            if trajectory is not None:
                selected = session.split_into_trajectories()[trajectory]
                history = session.through(selected) if through else selected
            execute_request(**options, history=history)
    except (ValueError, TypeError, OSError, KeyError, IndexError, ImportError, AttributeError, ReflectionError) as exc:
        raise click.ClickException(str(exc)) from exc


@reflection.command("show")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--reflection-id")
def show_command(path, reflection_id):
    """Verify and display saved resource Reflection artifacts."""
    try:
        paths = sorted(path.rglob("reflection.json")) if path.is_dir() else [path]
        findings = []
        for item in paths:
            result = Reflection.load(item)
            if reflection_id is None or result.id == reflection_id:
                findings.append(json.loads(item.read_text()))
        if not findings:
            raise ValueError("No matching saved reflections found")
        click.echo(json.dumps({"reflections": findings}, indent=2, ensure_ascii=False))
    except (ValueError, TypeError, OSError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc

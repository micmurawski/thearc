"""CLI for prompt reflection and immutable-history handoff."""

import importlib
import json
from contextlib import ExitStack
from pathlib import Path

import click

from thearc.learning.reflection.runtime import ReflectionError
from thearc.models import MetaAgent
from thearc.resources import Reflection, Reflector, SessionStore
from thearc.resources.sessions import DEFAULT_INDEX

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
        click.option(
            "--prompt-file",
            required=True,
            type=click.Path(exists=True, dir_okay=False, path_type=Path),
            help="Complete operator prompt; include formatted evidence for prompt reflection.",
        ),
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


def execute_request(
    *,
    implementation,
    model,
    prompt_file,
    agent_path,
    agent_mode,
    schema_path,
    validate_resources,
    runner,
    timeout,
    output,
    execute,
    history=None,
    agent=None,
    tool_transport=None,
):
    inputs = [prompt_file, agent_path, schema_path]
    destination = output.resolve()
    if any(part.is_symlink() for part in (output.absolute(), *output.absolute().parents)):
        raise ValueError("Output must not contain symlinks")
    if any(p and (p.resolve() == destination or (p.is_dir() and p.resolve() in destination.parents)) for p in inputs):
        raise ValueError("Output must be outside input paths")
    reflector = Reflector(
        implementation=implementation,
        model=model,
        runner=runner_for(implementation, runner),
        timeout_seconds=timeout,
        artifact_dir=output,
        **({"tool_transport": tool_transport} if tool_transport else {}),
    )
    prompt = prompt_file.read_text(encoding="utf-8")
    agent = load_agent(agent_path) or agent
    schema = json.loads(schema_path.read_text()) if schema_path else None
    tools = None
    if tool_transport:
        from thearc.resources import ReflectionTools, TaggedReflection

        if agent is None:
            raise ValueError("Reflection tools require --agent or a collector batch with an agent snapshot")
        tools = ReflectionTools(
            agent=agent,
            evidence=history,
            reflection_schema=schema or TaggedReflection,
            output=output / "submission.json",
        )
        options = {"tools": tools, "agent_mode": agent_mode, "validate_resources": validate_resources}
    else:
        options = {
            "agent": agent,
            "agent_mode": agent_mode,
            "output_schema": schema,
            "validate_resources": validate_resources,
        }
    prepared = reflector.prepare(prompt, **options)
    if history is not None:
        prepared["history"] = {
            "artifact_id": (history.session if hasattr(history, "session") else history).artifact_id,
            "event_ids": [e.id for e in history.selected_events],
        }
    output.mkdir(parents=True, exist_ok=False)
    preview = {key: value for key, value in prepared.items() if key not in {"agent", "tools"}}
    (output / "preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2) + "\n")
    if execute:
        result = (
            reflector.reflect(prompt, **options) if history is None else reflector.handoff(prompt, history, **options)
        )
        if tools is not None:
            findings = [item.output.model_dump(mode="json") for item in result]
            (output / "reflections.json").write_text(json.dumps(findings, ensure_ascii=False, indent=2) + "\n")
            click.echo(json.dumps({"accepted": len(findings), "reflections": str(output / "reflections.json")}))
        else:
            click.echo(json.dumps({"id": result.id, "text": result.text}, ensure_ascii=False, indent=2))
    else:
        click.echo(json.dumps({"status": "prepared", "preview": str(output / "preview.json")}))


@click.group()
def reflection():
    """Reflect on an operator prompt or continue captured session history."""


@reflection.command("serve")
@click.option("--agent", "agent_path", required=True, type=click.Path(exists=True, path_type=Path))
@click.option(
    "--schema",
    "schema_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="JSON Schema for one reflection. Defaults to TaggedReflection.",
)
@click.option(
    "--evidence",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="JSON array of canonical events; alternative to --index and --session-id.",
)
@click.option("--index", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--session-id", help="Canonical indexed session ID.")
@click.option("--trajectory", type=click.IntRange(min=0))
@click.option(
    "--ledger",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Optional assessment ledger for current and lifetime resource ranks.",
)
@click.option("--output", required=True, type=click.Path(path_type=Path), help="New submission JSON file.")
@click.option("--max-submissions", default=3, type=click.IntRange(min=1), show_default=True)
def serve_command(agent_path, schema_path, evidence, index, session_id, trajectory, ledger, output, max_submissions):
    """Expose resource lookup and validated reflection submission as stdio MCP tools."""
    from thearc.learning.curation.assessments import AssessmentLedger
    from thearc.resources import ReflectionTools, TaggedReflection
    from thearc.resources.reflection_mcp import serve_reflections

    try:
        if evidence is not None:
            if index is not None or session_id is not None or trajectory is not None:
                raise ValueError("Use --evidence or --index/--session-id, not both")
        elif index is None or session_id is None:
            raise ValueError("Supply --evidence or both --index and --session-id")
        with ExitStack() as stack:
            if evidence is not None:
                history = json.loads(evidence.read_text(encoding="utf-8"))
            else:
                store = stack.enter_context(SessionStore(index))
                history = store.get(session_id)
                if trajectory is not None:
                    history = history.split_into_trajectories()[trajectory]
            tools = ReflectionTools(
                agent=load_agent(agent_path),
                evidence=history,
                reflection_schema=json.loads(schema_path.read_text()) if schema_path else TaggedReflection,
                ledger=AssessmentLedger.model_validate_json(ledger.read_text()) if ledger else None,
                output=output,
                max_submissions=max_submissions,
            )
            serve_reflections(tools)
    except (ValueError, TypeError, OSError, KeyError, IndexError, ImportError, ReflectionError) as exc:
        raise click.ClickException(str(exc)) from exc


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
@click.option("--index", type=click.Path(dir_okay=False, path_type=Path), default=DEFAULT_INDEX, show_default=True)
@click.option(
    "--source",
    "sources",
    multiple=True,
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Transcript directory or collector batch to register and sync first; repeatable.",
)
@click.option(
    "--session-id",
    "session",
    help="Indexed, native or collector run ID, or a unique prefix; default: the most recent session.",
)
@click.option("--trajectory", type=click.IntRange(min=0), help="Zero-based trajectory index.")
@click.option("--through", is_flag=True, help="Include the session prefix through the selected trajectory.")
@click.option(
    "--tools",
    "tool_transport",
    type=click.Choice(["host", "mcp"]),
    help="Submit findings through ReflectionTools (built-in Codex only).",
)
def handoff_command(index, sources, session, trajectory, through, tool_transport, **options):
    """Append the reflection prompt to a captured session or trajectory."""
    from thearc.resources import Sessions

    try:
        if through and trajectory is None:
            raise ValueError("--through requires --trajectory")
        if options["output"].resolve() == index.expanduser().resolve():
            raise ValueError("Output must be outside the index")
        with Sessions.open(index) as store:
            for source in sources:
                store.add(source)
            selected = store.get(session)
            agent = store.agent(selected)
        history = selected
        if trajectory is not None:
            chosen = selected.split_into_trajectories()[trajectory]
            history = selected.through(chosen) if through else chosen
        execute_request(**options, history=history, agent=agent, tool_transport=tool_transport)
    except (
        ValueError,
        TypeError,
        OSError,
        KeyError,
        IndexError,
        LookupError,
        ImportError,
        AttributeError,
        ReflectionError,
    ) as exc:
        raise click.ClickException(str(exc)) from exc


@reflection.command("resources")
@click.option(
    "--agent",
    "agent_path",
    type=click.Path(exists=True, path_type=Path),
    help="Workspace directory or MetaAgent JSON (e.g. a batch's runs/<id>/agent-before.json).",
)
@click.option("--index", type=click.Path(dir_okay=False, path_type=Path), default=DEFAULT_INDEX, show_default=True)
@click.option("--session-id", "session", help="Use the agent recorded for this collector-batch session instead.")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["markdown", "json", "files"]),
    default="markdown",
    show_default=True,
    help="files: write the agent files a reflector reads and cites into --output.",
)
@click.option(
    "--outline", is_flag=True, help="Markdown: list headings with their file locations instead of full content."
)
@click.option("--ranks", is_flag=True, help="Include current ranks (and lifetime ranks when they differ).")
@click.option("--output", type=click.Path(path_type=Path), help="Write here instead of stdout.")
def resources_command(agent_path, index, session, output_format, outline, ranks, output):
    """Show the agent as Markdown with every resource and section labelled by file location (and ranks)."""
    from thearc.resources import ResourceView, Sessions

    try:
        if (agent_path is None) == (session is None):
            raise ValueError("Supply exactly one of --agent or --session-id")
        if agent_path is not None:
            agent = load_agent(agent_path)
        else:
            with Sessions.open(index) as store:
                agent = store.agent(store.get(session))
            if agent is None:
                raise ValueError("That session has no recorded agent; use --agent")
        if output_format == "files":
            if output is None:
                raise ValueError("--format files requires --output DIRECTORY")
            agent.files().write(output)
            click.echo(f"Wrote {output}")
            return
        view = ResourceView(agent)
        if output_format == "markdown":
            text = view.catalog_markdown(include_ranks=ranks, include_content=not outline, title=f"Agent: {agent.name}")
        else:
            text = json.dumps(view.catalog(include_ranks=ranks), ensure_ascii=False, indent=2) + "\n"
        if output is None:
            click.echo(text, nl=False)
        else:
            with output.open("x", encoding="utf-8") as stream:
                stream.write(text)
            click.echo(f"Wrote {output}")
    except (ValueError, TypeError, OSError, LookupError) as exc:
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

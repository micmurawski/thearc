from typing import Any

import click


class AliasedGroup(click.Group):
    """A group whose subcommands may declare ``aliases``; they resolve to the command and show in help.

    Aliases are declared on the subcommand itself: ``@group.command("list", aliases=["ls"])``, or
    ``@click.group(cls=AliasedGroup, aliases=["s"])`` for a group added to an ``AliasedGroup`` parent.
    """

    group_class = type  # subgroups created with ``.group()`` are AliasedGroups too

    def __init__(self, *args: Any, aliases: tuple[str, ...] | list[str] = (), **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.aliases = list(aliases)

    def command(self, *args: Any, aliases: tuple[str, ...] | list[str] = (), **kwargs: Any) -> Any:
        if args and callable(args[0]):  # bare ``@group.command``
            return super().command(*args, **kwargs)
        decorator = super().command(*args, **kwargs)

        def wrap(f: Any) -> click.Command:
            cmd = decorator(f)
            cmd.aliases = list(aliases)
            return cmd

        return wrap

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        cmd = super().get_command(ctx, cmd_name)
        if cmd is not None:
            return cmd
        for cmd in self.commands.values():
            if cmd_name in getattr(cmd, "aliases", ()):
                return cmd
        return None

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        # Report the canonical name so usage and error messages don't show the alias.
        _, cmd, args = super().resolve_command(ctx, args)
        return cmd.name if cmd else None, cmd, args

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows = []
        for name in self.list_commands(ctx):
            cmd = self.get_command(ctx, name)
            if cmd is None or cmd.hidden:
                continue
            aliases = getattr(cmd, "aliases", ())
            rows.append((f"{name} ({', '.join(aliases)})" if aliases else name, cmd))
        if rows:
            limit = formatter.width - 6 - max(len(label) for label, _ in rows)
            with formatter.section("Commands"):
                formatter.write_dl([(label, cmd.get_short_help_str(limit)) for label, cmd in rows])

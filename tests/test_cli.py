from click.testing import CliRunner
from thearc.cli import main

def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output

def test_cli_list():
    runner = CliRunner()
    result = runner.invoke(main, ["list"])
    assert result.exit_code == 0
    assert "uploadcontext" in result.output

def test_cli_status(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["status", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "ANTIGRAVITY" in result.output
    assert "CLAUDE" in result.output
    assert "CODEX" in result.output

def test_cli_install_project(tmp_path):
    runner = CliRunner()
    result = runner.invoke(main, ["install", "--project", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "ANTIGRAVITY" in result.output
    assert "Installation completed successfully!" in result.output

def test_cli_uploadcontext_dry_run(tmp_path):
    dummy_log = tmp_path / "test_transcript.jsonl"
    dummy_log.write_text("{\"event\": \"test\"}")

    runner = CliRunner()
    result = runner.invoke(main, ["uploadcontext", "workspace=test,name=session", "--file", str(dummy_log), "--dry-run"])
    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert "s3://thearc-contexts/test/session/" in result.output

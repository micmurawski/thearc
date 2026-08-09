from pathlib import Path
from typing import List, Dict, Any, Optional
import os
import shutil

from thearc.agents import get_installer, get_all_installers, BaseAgentInstaller
from thearc.config import SUPPORTED_AGENTS

BUNDLED_DIR = Path(__file__).parent.parent / "bundled"

class PluginInstaller:
    """Plugin installer orchestrator for local projects and global agent configs."""

    def __init__(self, bundled_dir: Path = BUNDLED_DIR):
        self.bundled_dir = bundled_dir
        self.bundled_skills_dir = self.bundled_dir / "skills"
        self.bundled_prompts_dir = self.bundled_dir / "prompts"

    def list_bundled_skills(self) -> List[str]:
        """List names of skills bundled with thearc."""
        if not self.bundled_skills_dir.exists():
            return []
        return [d.name for d in self.bundled_skills_dir.iterdir() if d.is_dir()]

    def list_bundled_commands(self) -> List[str]:
        """List names of slash commands bundled with thearc."""
        if not self.bundled_prompts_dir.exists():
            return []
        return [f.stem for f in self.bundled_prompts_dir.glob("*.md")]

    def install(
        self,
        target_scope: str = "project",
        agent_name: str = "all",
        skill_name: Optional[str] = None,
        project_dir: Path = Path("."),
        force: bool = False
    ) -> Dict[str, List[Path]]:
        """
        Install skills and commands for specified agent(s).

        :param target_scope: 'project' or 'global'
        :param agent_name: 'antigravity', 'claude', 'codex', or 'all'
        :param skill_name: Specific skill to install, or None for all bundled skills
        :param project_dir: Target project directory (for scope='project')
        :param force: Overwrite existing installed files
        :return: Dict mapping agent name to list of installed file Paths
        """
        installers: List[BaseAgentInstaller] = []
        if agent_name.lower() == "all":
            installers = get_all_installers()
        else:
            installers = [get_installer(agent_name)]

        results: Dict[str, List[Path]] = {}

        skills_to_install = []
        if skill_name:
            skill_path = self.bundled_skills_dir / skill_name
            if skill_path.exists():
                skills_to_install.append((skill_name, skill_path))
            else:
                raise FileNotFoundError(f"Bundled skill '{skill_name}' not found at {skill_path}")
        else:
            if self.bundled_skills_dir.exists():
                for s_dir in self.bundled_skills_dir.iterdir():
                    if s_dir.is_dir():
                        skills_to_install.append((s_dir.name, s_dir))

        commands_to_install = []
        if self.bundled_prompts_dir.exists():
            for c_file in self.bundled_prompts_dir.glob("*.md"):
                commands_to_install.append((c_file.stem, c_file))

        for installer in installers:
            agent_installed_files: List[Path] = []

            # 1. Install Skills
            for s_name, s_path in skills_to_install:
                files = installer.install_skill(
                    skill_name=s_name,
                    source_dir=s_path,
                    target_scope=target_scope,
                    project_dir=project_dir,
                    force=force
                )
                agent_installed_files.extend(files)

            # 2. Install Commands/Prompts
            for c_name, c_path in commands_to_install:
                file_path = installer.install_command(
                    command_name=c_name,
                    source_file=c_path,
                    target_scope=target_scope,
                    project_dir=project_dir,
                    force=force
                )
                agent_installed_files.append(file_path)

            results[installer.name] = agent_installed_files

        return results

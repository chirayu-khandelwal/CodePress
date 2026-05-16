# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click",
#     "pyfiglet",
#     "pyperclip",
# ]
# ///
"""CodePress — compress a codebase into a single AI-ready text file."""

from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

import click
import pyfiglet
import pyperclip

logger = logging.getLogger("codepress")

# ── Constants ────────────────────────────────────────────────────────────

IGNORED_DIRS: frozenset[str] = frozenset({
    ".git", ".svn", ".hg",
    "node_modules", "vendor", "packages", "deps",
    "lib", "gems", "bundle", "Pods", "Carthage",
    "gradle", "mvnw", ".gradle", ".m2", "target",
    "__pycache__", ".venv", "venv", ".env",
    "dist", "build", "out",
    ".vscode", ".idea", ".eclipse",
    ".next", ".nuxt", ".svelte-kit",
})

SUPPORTED_EXTS: frozenset[str] = frozenset({
    "py", "cpp", "c", "h", "hpp", "java", "js", "ts", "jsx", "tsx",
    "cs", "go", "rs", "rb", "php", "swift", "kt", "kts", "scala",
    "pl", "pm", "lua", "r", "dart", "html", "htm", "css", "scss",
    "vue", "svelte", "sh", "bash", "zsh", "ps1", "bat", "cmd",
    "hs", "jl", "sql", "m", "mm", "ex", "exs", "vb", "fs", "fsx",
    "groovy", "erl", "hrl", "zig", "nim", "v", "tf", "proto",
    "json", "xml", "yaml", "yml", "toml", "ini", "cfg", "conf",
    "md", "txt", "rst",
})

AI_PROMPT = """You are an expert coding agent with context-aware understanding of software projects. While you're designed to analyze entire codebases, *only a subset of files and the project structure* has been provided due to context window constraints.

*Your responsibilities:*
1. *Accurately execute tasks* (e.g., debugging, documentation, feature implementation) *using ONLY the files currently in context*.
2. *Explicitly request missing files* when needed:
   - State exactly which file(s) you require (using full paths from the provided project structure)
   - Justify why the file is essential for the task
   - Never assume file existence beyond the provided context
3. *Prioritize solutions within scope*: If a task can be completed with available files, do so without requesting additions.

*Critical rules:*
- ❌ *NEVER* invent code from unprovided files
- ❌ *NEVER* guess file contents/structure
- ✅ *ALWAYS* reference the project structure when requesting files
- ✅ *ALWAYS* clarify ambiguities before proceeding

Due to context limits, the code is compressed by removing newlines and tabs when sent. However, when responding, debugging, or providing code, always use normal, readable formatting that can be copied and pasted directly.

"""


# ── Data types ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FileEntry:
    """A single file discovered during collection."""
    abs_path: Path
    rel_path: Path


@dataclass
class FileSet:
    """Result of collecting files from a project directory."""
    included: list[FileEntry] = field(default_factory=list)
    skipped_dirs: list[Path] = field(default_factory=list)
    skipped_files: list[Path] = field(default_factory=list)

    @property
    def total_files(self) -> int:
        return len(self.included)


@dataclass(frozen=True)
class ProcessResult:
    """Result of processing a codebase."""
    output_path: Path
    file_set: FileSet
    size_kb: float
    estimated_tokens: int


# ── Banner ───────────────────────────────────────────────────────────────

def print_banner() -> None:
    """Print a styled CodePress banner or a simple fallback."""
    try:
        ascii_art = pyfiglet.figlet_format("CodePress", font="slant")
        visible = [line for line in ascii_art.splitlines() if line.strip()]
        width = click.get_terminal_size().columns
        reset = "\033[0m"
        colors = ["\033[1;38;5;81m", "\033[1;38;5;75m", "\033[1;38;5;69m", "\033[1;38;5;63m"]
        border = "\033[1;38;5;39m"

        print(f'\n{border}{"═" * width}{reset}')
        for i, line in enumerate(visible):
            color = colors[i % len(colors)]
            print(f"{border}║{color} {line[:width - 5]:<{width - 6}}{reset}{border}║{reset}")
        print(f'{border}{"◆" * min(40, width - 4):^{width - 4}}{reset}')
        for tag in [
            "Press your codebase into an AI-ready prompt",
            "Works with ChatGPT, Claude, Qwen, and more",
        ]:
            print(f'{border}║{tag:^{width - 4}}║{reset}')
        print(f'{border}{"═" * width}{reset}\n')
    except Exception:
        click.echo("⚡ CodePress — Codebase → AI-Ready Prompt\n")


# ── Directory tree ───────────────────────────────────────────────────────

def generate_tree(root: Path, ignored_dirs: frozenset[str]) -> str:
    """Generate an ASCII tree of the directory structure."""
    if not root.is_dir():
        return f"{root} is not a directory\n"

    lines: list[str] = []

    def _walk(path: Path, prefix: str = "") -> None:
        try:
            entries = sorted(
                (p for p in path.iterdir() if p.name not in ignored_dirs),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except PermissionError:
            return

        for idx, entry in enumerate(entries):
            is_last = idx == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(f"{prefix}{connector}{entry.name}")

            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension)

    _walk(root)
    return "\n".join(lines) + "\n" if lines else ""


# ── File collector ───────────────────────────────────────────────────────

class FileCollector:
    """Walk a directory once, apply all filters, return a clean FileSet."""

    def __init__(
        self,
        root: Path,
        extra_extensions: frozenset[str] = frozenset(),
        ignored_dirs: frozenset[str] = frozenset(),
        ignored_files: frozenset[Path] = frozenset(),
        forced_files: list[Path] | None = None,
    ):
        self.root = root.resolve()
        self.extensions = SUPPORTED_EXTS | extra_extensions
        self.ignored_dirs = IGNORED_DIRS | ignored_dirs
        self.ignored_files = {f.resolve() for f in ignored_files}
        self.forced_files = [f.resolve() for f in (forced_files or [])]

    def collect(self) -> FileSet:
        """Collect all matching files under root."""
        result = FileSet()

        for dirpath, dirnames, filenames in os.walk(self.root, topdown=True):
            base = Path(dirpath)

            # Prune ignored directories in-place
            pruned = []
            for d in dirnames:
                if d in self.ignored_dirs:
                    result.skipped_dirs.append(base / d)
                else:
                    pruned.append(d)
            dirnames[:] = pruned

            for fname in filenames:
                fpath = base / fname
                abs_path = fpath.resolve()

                if abs_path in self.ignored_files:
                    result.skipped_files.append(fpath)
                    continue

                ext = fpath.suffix.lstrip(".").lower()
                if ext in self.extensions:
                    result.included.append(FileEntry(
                        abs_path=abs_path,
                        rel_path=fpath.relative_to(self.root),
                    ))

        # Add forced files not already collected
        collected = {e.abs_path for e in result.included}
        for fpath in self.forced_files:
            if fpath not in collected and fpath.is_file():
                result.included.append(FileEntry(
                    abs_path=fpath,
                    rel_path=fpath.relative_to(self.root) if fpath.is_relative_to(self.root) else fpath.name,
                ))

        return result


# ── Output formatter ─────────────────────────────────────────────────────

class OutputFormatter:
    """Build the consolidated output file with begin/add/end interface."""

    SEPARATOR = "-" * 60
    SECTION_DIV = "=" * 80

    def __init__(self, output_path: Path, project_root: Path):
        self.output_path = output_path
        self.project_root = project_root
        self._file = None

    def begin(self, directory_tree: str) -> None:
        """Write the header with AI prompt and project structure."""
        self._file = open(self.output_path, "w", encoding="utf-8")
        self._file.write(f"{AI_PROMPT}project : {self.project_root}\n\n")
        self._file.write("PROJECT STRUCTURE:\n")
        self._file.write(self.SEPARATOR + "\n")
        self._file.write(directory_tree)
        self._file.write("\n" + self.SECTION_DIV + "\n")
        self._file.write("FILE CONTENTS:\n")
        self._file.write(self.SECTION_DIV + "\n")

    def add_file(self, entry: FileEntry, content: str) -> None:
        """Append a single file's content."""
        self._file.write(f"\n{self.SEPARATOR}\n")
        self._file.write(f"📁 File: {entry.rel_path}\n")
        self._file.write(f"{self.SEPARATOR}\n")
        self._file.write(f"## File content:\n{content}\n")

    def end(self) -> Path:
        """Close, compress, return output path."""
        self._file.close()
        self._compress()
        return self.output_path

    def _compress(self) -> None:
        """Remove newlines and reduce consecutive tabs."""
        content = self.output_path.read_text(encoding="utf-8")
        content = content.replace("\n", "")
        content = re.sub(r"\t{2,}", "\t", content)
        self.output_path.write_text(content, encoding="utf-8")


# ── Processor ────────────────────────────────────────────────────────────

class CodebaseProcessor:
    """Orchestrate collection, formatting, and compression of a codebase."""

    def __init__(
        self,
        source_directory: Path,
        extra_extensions: list[str] | None = None,
        ignored_dirs: list[str] | None = None,
        ignored_files: list[str] | None = None,
        forced_files: list[str] | None = None,
        output_file: str | None = None,
    ):
        self.source_directory = source_directory.resolve()
        self.output_path = Path(output_file).resolve() if output_file else self.source_directory.parent / f"{self.source_directory.name}_codebase.txt"

        extra_ext = frozenset(e.lstrip(".").lower() for e in (extra_extensions or []))
        ignored_dir_set = frozenset(Path(d).name for d in (ignored_dirs or []))
        ignored_file_set = frozenset(
            (self.source_directory / f).resolve() for f in (ignored_files or [])
        )
        forced = [
            (self.source_directory / f).resolve() if not Path(f).is_absolute() else Path(f).resolve()
            for f in (forced_files or [])
        ]

        self.collector = FileCollector(
            root=self.source_directory,
            extra_extensions=extra_ext,
            ignored_dirs=ignored_dir_set,
            ignored_files=ignored_file_set,
            forced_files=forced,
        )

    def process(self) -> ProcessResult:
        """Run the full pipeline: collect → format → compress."""
        file_set = self.collector.collect()
        tree = generate_tree(self.source_directory, self.collector.ignored_dirs)

        formatter = OutputFormatter(self.output_path, self.source_directory)
        formatter.begin(tree)

        for entry in file_set.included:
            try:
                content = entry.abs_path.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                click.echo(click.style(f"  ⚠️  Could not read {entry.rel_path}: {e}", fg="yellow"))
                continue
            formatter.add_file(entry, content)

        formatter.end()

        size_kb = self.output_path.stat().st_size / 1024
        estimated_tokens = len(self.output_path.read_text(encoding="utf-8")) // 4

        return ProcessResult(
            output_path=self.output_path,
            file_set=file_set,
            size_kb=size_kb,
            estimated_tokens=estimated_tokens,
        )


# ── CLI ──────────────────────────────────────────────────────────────────

@click.command(
    context_settings=dict(help_option_names=["-h", "--help"]),
    epilog="""
\b
EXAMPLES:
    Basic usage (creates myproject_codebase.txt):
        $ CodePress myproject

    Include additional file types:
        $ CodePress myproject -e md -e yaml -e txt

    Exclude specific directories:
        $ CodePress myproject --ignore-dir tests --ignore-dir __pycache__

    Exclude specific files:
        $ CodePress myproject -i config.py -i secrets.json

    Include non-code files and set custom output:
        $ CodePress myproject -f README.md -f docs/notes.txt -o complete_project.txt

    Copy result directly to clipboard:
        $ CodePress myproject --copy

    Complex example with multiple options:
        $ CodePress myproject -e md -e yaml -i config.py --ignore-dir tests -f LICENSE -o project_export.txt --copy

\b
SUPPORTED FILE TYPES (by default):
    Programming: .py .js .ts .java .cpp .c .cs .go .rs .rb .php .swift .kt .scala
    Web: .html .css .jsx .tsx .vue .svelte .scss
    Scripts: .sh .bash .ps1 .bat .cmd
    Data: .sql .json .xml .yaml .yml .toml
    And many more...

\b
NOTES:
    • Files are compressed (whitespace removed) to save tokens
    • Output includes project structure tree and file contents
    • Perfect for sharing with AI assistants like ChatGPT, Claude, or GitHub Copilot
    • Use --copy to get a ready-to-paste prompt for AI tools
""",
)
@click.argument("directory", type=click.Path(exists=True, file_okay=False, dir_okay=True, path_type=Path), metavar="PROJECT_DIR")
@click.option("-e", "--extra-extensions", multiple=True, metavar="EXT", help="Additional file extensions (without dot).")
@click.option("-i", "--ignore", multiple=True, metavar="FILE", help="Exclude specific files.")
@click.option("--ignore-dir", "--ignore-directory", "ignore_directory", multiple=True, metavar="DIR", help="Exclude directories.")
@click.option("-f", "--add-files", multiple=True, metavar="FILE", help="Force-include specific files.")
@click.option("-o", "--output", type=click.Path(path_type=Path), metavar="FILENAME", help="Custom output filename.")
@click.option("-c", "--copy", is_flag=True, help="Copy output to clipboard as an AI-ready prompt.")
def cli(directory, extra_extensions, ignore, ignore_directory, add_files, output, copy):
    """Transform your entire codebase into a single, AI-friendly text file."""

    processor = CodebaseProcessor(
        source_directory=directory,
        extra_extensions=list(extra_extensions),
        ignored_dirs=list(ignore_directory),
        ignored_files=list(ignore),
        forced_files=list(add_files),
        output_file=str(output) if output else None,
    )

    result = processor.process()

    click.echo(click.style("\n✓ Codebase processed", fg="green", bold=True))
    click.echo(f"  Files collected: {result.file_set.total_files}")
    click.echo(f"  Directories skipped: {len(result.file_set.skipped_dirs)}")
    click.echo(f"  Files skipped: {len(result.file_set.skipped_files)}")

    click.echo(f"\n{click.style('📄 Output:', fg='cyan', bold=True)}")
    click.echo(f"  {result.output_path}")
    click.echo(click.style(f"  Size: {result.size_kb / 1024:.2f} MB", fg="blue"))
    click.echo(click.style(f"  Estimated tokens: {result.estimated_tokens:,}", fg="magenta"))

    if copy:
        text = result.output_path.read_text(encoding="utf-8")
        pyperclip.copy(text + "\n\nQuery: [provide your query]")
        click.echo(click.style("\n✓ Copied to clipboard!", fg="cyan"))


if __name__ == "__main__":
    cli()

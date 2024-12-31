import os
import pathlib
import click
import re

from gitingest_lite.constant import MAX_FILE_SIZE

from .encoding import setup_encoding

# Setup encoding first
setup_encoding()

# Define constants

def sanitize_filename(name: str) -> str:
    """Sanitize a string to make it a valid filename."""
    return re.sub(r'[\\/*?:"<>|]', '_', name).strip('_')


def get_project_name(source: str) -> str:
    """Get a clean project name from the source path or URL."""
    if 'github.com' in source:
        # Handle both URLs and paths containing 'github.com'
        normalized_path = source.replace('\\', '/')
        path_parts = normalized_path.split('/')
        
        try:
            # Find the index of 'github.com'
            github_index = path_parts.index('github.com')
            
            # Extract owner and repository name after 'github.com'
            if len(path_parts) > github_index + 2:
                project_name = f"{path_parts[github_index+1]}_{path_parts[github_index+2]}"
            else:
                project_name = "unknown_repo"
        except ValueError:
            project_name = "unknown_repo"
    else:
        # For local paths, get the base folder name
        project_name = os.path.basename(os.path.abspath(source))
    
    # Ensure valid project name
    if not project_name.strip():
        project_name = "default_project"
    
    # Sanitize filename to remove invalid characters
    project_name = sanitize_filename(project_name)
    
    # Ensure the filename ends with '.txt' (only if not already present)
    if not project_name.endswith('txt'):
        project_name += '.txt'
    
    return project_name


@click.command()
@click.argument("source", type=str, required=True)
@click.option(
    "--output", "-o", default=None,
    help="Output file path (default: export/[project_name].txt)"
)
@click.option(
    "--max-size", "-s", default=MAX_FILE_SIZE,
    help="Maximum file size to process in bytes"
)
@click.option(
    "--exclude-pattern", "-e", multiple=True,
    help="Patterns to exclude"
)
@click.option(
    "--include-pattern", "-i", multiple=True,
    help="Patterns to include"
)
def main(
    source: str,
    output: str | None,
    max_size: int,
    exclude_pattern: tuple[str, ...],
    include_pattern: tuple[str, ...],
) -> None:
    """Analyze a directory and create a text dump of its contents."""
    version = "0.1.0"
    print(f"Running gitingest_lite as a script...{version}")
    try:
        from gitingest_lite.ingest import ingest

        # Resolve the source path
        source = str(pathlib.Path(source).resolve())

        # Handle patterns
        exclude_patterns = list(exclude_pattern)
        include_patterns = list(set(include_pattern))

        # Determine project name from source
        project_name = get_project_name(source)

        # Ensure 'export' directory exists
        export_dir = "export"
        os.makedirs(export_dir, exist_ok=True)

        # Set default output path if not specified
        if not output:
            output = os.path.join(export_dir, f"{project_name}")
        else:
            output = str(pathlib.Path(output).resolve())

        # Call ingest function
        summary, tree, content = ingest(
            source,
            max_file_size=max_size,
            include_patterns=include_patterns,
            exclude_patterns=exclude_patterns,
            output=output
        )

        # Write output with explicit UTF-8 encoding
        with open(output, 'w', encoding='utf-8', errors='replace') as f:
            if isinstance(summary, bytes):
                summary = summary.decode('utf-8', errors='replace')
            if isinstance(tree, bytes):
                tree = tree.decode('utf-8', errors='replace')
            if isinstance(content, bytes):
                content = content.decode('utf-8', errors='replace')

            f.write(f"{summary}\n\n{tree}\n\n{content}")

        click.echo(f"\n✅ Analysis complete! Output written to: {output}")
        click.echo("\n📊 Summary:")
        click.echo(summary)

    except Exception as e:
        click.echo(f"❌ Error: {str(e)}", err=True)
        raise click.Abort()


if __name__ == "__main__":
    main()

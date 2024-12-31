import os
import sys
import pathlib
import shutil
import subprocess
import tempfile
import atexit
import time
import gc
from typing import Union, Tuple
from urllib.parse import urlparse
import tiktoken

class Colors:
    """ANSI color codes for console output"""
    BLACK = "\033[0;30m"
    RED = "\033[0;31m"
    GREEN = "\033[0;32m"
    BROWN = "\033[0;33m"
    BLUE = "\033[0;34m"
    PURPLE = "\033[0;35m"
    CYAN = "\033[0;36m"
    WHITE = "\033[1;37m"
    YELLOW = "\033[1;33m"
    END = "\033[0m"

DEFAULT_IGNORE_PATTERNS = [
    "*.pyc", "__pycache__", "node_modules",
    "*.class", "target/", "dist/", "build/",
    "*.jar", "*.war", "*.ear", "*.zip",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.ico",
    "*.pdf", "*.mov", "*.mp4", "*.mp3", "*.wav"
]

def log_info(message: str) -> None:
    """Print info message with color"""
    print(f"{Colors.GREEN}INFO{Colors.END}: {message}")

def log_warn(message: str) -> None:
    """Print warning message with color"""
    print(f"{Colors.BROWN}WARN{Colors.END}: {message}")

def log_error(message: str) -> None:
    """Print error message with color"""
    print(f"{Colors.RED}ERROR{Colors.END}: {message}")

def setup_encoding():
    """Ensure proper UTF-8 encoding."""
    if sys.stdout.encoding != 'utf-8':
        sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)
    if sys.stderr.encoding != 'utf-8':
        sys.stderr = open(sys.stderr.fileno(), mode='w', encoding='utf-8', buffering=1)

def parse_github_url(url: str) -> tuple[str, str, str]:
    """Parse GitHub URL into components."""
    log_info(f"Parsing URL: {Colors.CYAN}{url}{Colors.END}")
    
    # Add https:// if missing
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
        
    parsed = urlparse(url)
    path_parts = parsed.path.strip('/').split('/')
    
    if len(path_parts) < 2:
        raise ValueError("Invalid GitHub URL format")
        
    user = path_parts[0]
    repo = path_parts[1]
    branch = 'main'  # Default branch
    
    if len(path_parts) > 3 and path_parts[2] == 'tree':
        branch = path_parts[3]
    
    log_info(f"User: {user}, Repo: {repo}, Branch: {branch}")
    return user, repo, branch

def parse_gitignore(path: str) -> list[str]:
    """Parse .gitignore file and return patterns."""
    patterns = []
    gitignore_path = os.path.join(path, '.gitignore')
    
    if not os.path.exists(gitignore_path):
        log_info("No .gitignore file found")
        return patterns
        
    log_info(f"Reading .gitignore from: {gitignore_path}")
    
    try:
        with open(gitignore_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#'):
                    log_info(f"Adding ignore pattern: {line}")
                    if line.endswith('/'):
                        # For directory patterns
                        base = line.rstrip('/')
                        patterns.extend([
                            f"{base}",  # Match directory itself
                            f"{base}/**",  # Match all contents
                            f"**/{base}",  # Match directory in subdirectories
                            f"**/{base}/**"  # Match contents in subdirectories
                        ])
                    else:
                        # Handle file patterns
                        patterns.append(line)
                        if '.' not in line and not line.endswith('/'):
                            patterns.append(f"{line}/")
    except Exception as e:
        log_error(f"Error reading .gitignore: {str(e)}")
        return []
        
    return list(dict.fromkeys(patterns))  # Remove duplicates

def clone_repo(url: str, target_dir: str) -> None:
    """Clone a GitHub repository."""
    log_info(f"Cloning repository: {Colors.CYAN}{url}{Colors.END}")
    log_info(f"Target directory: {target_dir}")
    
    try:
        cmd = ['git', 'clone', '--depth=1', url, target_dir]
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        log_info("Repository cloned successfully")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to clone repository: {e.stderr}")

def is_text_file(file_path: str) -> bool:
    """Check if a file is a text file."""
    try:
        with open(file_path, "rb") as file:
            chunk = file.read(1024)
        return not bool(chunk.translate(None, bytes([7,8,9,10,12,13,27] + list(range(0x20, 0x100)))))
    except:
        return False

def should_process_file(filepath: str, ignore_patterns: list[str], include_patterns: list[str] = None) -> bool:
    """Determine if a file should be processed based on patterns."""
    from fnmatch import fnmatch
    
    # Convert path to relative for pattern matching
    try:
        relpath = os.path.relpath(filepath)
    except ValueError:
        # Handle cross-device path comparisons
        relpath = os.path.basename(filepath)
    
    # Check ignore patterns
    for pattern in ignore_patterns:
        if fnmatch(relpath, pattern):
            log_info(f"Skipping {Colors.YELLOW}{relpath}{Colors.END} (matched ignore pattern: {pattern})")
            return False
    
    # If include patterns specified, file must match one
    if include_patterns:
        should_include = any(fnmatch(relpath, pattern) for pattern in include_patterns)
        if not should_include:
            log_info(f"Skipping {Colors.YELLOW}{relpath}{Colors.END} (did not match any include patterns)")
        return should_include
    
    return True

def read_file_content(file_path: str, max_size: int) -> Union[str, None]:
    """Read file content with size limit."""
    try:
        if os.path.getsize(file_path) > max_size:
            log_warn(f"Skipping {Colors.YELLOW}{file_path}{Colors.END} (exceeds size limit)")
            return None
        
        if not is_text_file(file_path):
            log_info(f"Skipping {Colors.YELLOW}{file_path}{Colors.END} (not a text file)")
            return None
            
        log_info(f"Reading: {Colors.CYAN}{file_path}{Colors.END}")
        with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        log_error(f"Error reading {file_path}: {str(e)}")
        return None

def scan_directory(
    path: str,
    max_file_size: int = 1024 * 1024,  # 1MB default
    ignore_patterns: list[str] = None,
    include_patterns: list[str] = None
) -> Tuple[list[dict], int, int]:
    """Scan directory and collect file information."""
    log_info(f"Scanning directory: {Colors.CYAN}{path}{Colors.END}")
    
    if ignore_patterns is None:
        ignore_patterns = DEFAULT_IGNORE_PATTERNS
    
    # Add .gitignore patterns
    gitignore_patterns = parse_gitignore(path)
    ignore_patterns.extend(gitignore_patterns)
    
    files_info = []
    total_files = 0
    total_dirs = 0

    for root, dirs, files in os.walk(path):
        total_dirs += len(dirs)
        
        for file in files:
            filepath = os.path.join(root, file)
            
            if not should_process_file(filepath, ignore_patterns, include_patterns):
                continue
                
            content = read_file_content(filepath, max_file_size)
            if content is not None:
                files_info.append({
                    'path': os.path.relpath(filepath, path),
                    'content': content,
                    'size': os.path.getsize(filepath)
                })
                total_files += 1
                
    log_info(f"Found {Colors.YELLOW}{total_files}{Colors.END} files in {Colors.YELLOW}{total_dirs}{Colors.END} directories")
    return files_info, total_files, total_dirs

def create_tree_structure(path: str, files_info: list[dict]) -> str:
    """Create a tree-like representation of the file structure."""
    tree = "Directory structure:\n"
    
    # Create dict of paths and their levels
    paths = sorted([f['path'] for f in files_info])
    
    for path in paths:
        parts = path.split(os.sep)
        level = len(parts) - 1
        prefix = "    " * level
        tree += f"{prefix}{'└── ' if level > 0 else ''}{parts[-1]}\n"
        
    return tree

def estimate_tokens(text: str) -> str:
    """Estimate number of tokens in text."""
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
        token_count = len(encoding.encode(text))
        
        if token_count > 1_000_000:
            return f"{token_count/1_000_000:.1f}M"
        elif token_count > 1_000:
            return f"{token_count/1_000:.1f}k"
        return str(token_count)
    except:
        return "Unknown"

def ingest(
    source: str,
    output: str = "digest.txt",
    max_file_size: int = 1024 * 1024,
    ignore_patterns: list[str] = None,
    include_patterns: list[str] = None
) -> Tuple[str, str, str]:
    """
    Main function to analyze a directory or GitHub repository and create digest.
    
    Args:
        source: Directory path or GitHub URL to analyze
        output: Output file path (default: digest.txt)
        max_file_size: Maximum file size to process in bytes
        ignore_patterns: List of patterns to ignore
        include_patterns: List of patterns to include
        
    Returns:
        Tuple of (summary, tree, content)
    """
    setup_encoding()
    temp_dir = None
    original_cwd = os.getcwd()  # Store original working directory
    
    def cleanup_temp_dir():
        """Helper function to clean up temporary directory"""
        nonlocal temp_dir, original_cwd
        if temp_dir and os.path.exists(temp_dir):
            try:
                # Change back to original directory to ensure we're not in the temp dir
                os.chdir(original_cwd)
                
                # Give some time for file handles to be released
                time.sleep(1)
                
                log_info(f"Starting cleanup of directory: {temp_dir}")
                
                # Force Python garbage collection to release file handles
                gc.collect()
                
                # Remove .git directory first to release Git handles
                git_dir = os.path.join(temp_dir, '.git')
                if os.path.exists(git_dir):
                    try:
                        # Use Git to clean up its own files
                        subprocess.run(['git', 'clean', '-fd'], 
                                    cwd=temp_dir, 
                                    capture_output=True, 
                                    check=False)
                        subprocess.run(['git', 'gc'], 
                                    cwd=temp_dir, 
                                    capture_output=True, 
                                    check=False)
                        # Remove .git directory directly
                        shutil.rmtree(git_dir, ignore_errors=True)
                    except Exception as e:
                        log_error(f"Error cleaning up git directory: {str(e)}")

                # Close any remaining file handles
                try:
                    if os.name == 'nt':  # Windows
                        subprocess.run(['handle.exe', '-c', temp_dir], 
                                    capture_output=True, 
                                    check=False)
                except:
                    pass  # handle.exe might not be available
                
                # Try running Windows' rd command directly
                if os.name == 'nt':
                    try:
                        subprocess.run(['cmd', '/c', f'rd /s /q "{temp_dir}"'],
                                    capture_output=True,
                                    check=False)
                    except Exception as e:
                        log_error(f"Failed to remove directory using rd: {str(e)}")

                # If directory still exists, try the previous cleanup methods
                if os.path.exists(temp_dir):
                    # First try to modify permissions on all files and directories
                    for root, dirs, files in os.walk(temp_dir, topdown=False):
                        for name in files:
                            filepath = os.path.join(root, name)
                            try:
                                os.chmod(filepath, 0o777)
                                log_info(f"Modified permissions for file: {filepath}")
                            except Exception as e:
                                log_error(f"Failed to modify permissions for file {filepath}: {str(e)}")
                        
                        for name in dirs:
                            dirpath = os.path.join(root, name)
                            try:
                                os.chmod(dirpath, 0o777)
                                log_info(f"Modified permissions for directory: {dirpath}")
                            except Exception as e:
                                log_error(f"Failed to modify permissions for directory {dirpath}: {str(e)}")
                    
                    # Try to remove all files first
                    for root, dirs, files in os.walk(temp_dir, topdown=False):
                        for name in files:
                            filepath = os.path.join(root, name)
                            try:
                                os.remove(filepath)
                                log_info(f"Removed file: {filepath}")
                            except Exception as e:
                                log_error(f"Failed to remove file {filepath}: {str(e)}")
                    
                    # Then attempt to remove the directory tree
                    try:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                    except Exception as e:
                        log_error(f"Failed to remove directory tree: {str(e)}")

                # Schedule the directory for deletion on reboot if all else fails
                if os.path.exists(temp_dir) and os.name == 'nt':
                    try:
                        subprocess.run(['cmd', '/c', f'move "{temp_dir}" "{temp_dir}_to_delete" && rd /s /q "{temp_dir}_to_delete"'],
                                    capture_output=True,
                                    check=False)
                    except Exception as e:
                        log_error(f"Failed to schedule directory for deletion: {str(e)}")
                    
            except Exception as e:
                log_error(f"Error during cleanup: {str(e)}")
                # Try one last time with a delay
                try:
                    time.sleep(2)
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except:
                    pass
    
    try:
        # Check if source is a GitHub URL
        if 'github.com' in source:
            print("Cloning GitHub repository...")
            user, repo, branch = parse_github_url(source)
            # Create temp directory with proper permissions for deletion
            current_dir = os.getcwd()
            temp_dir = tempfile.mkdtemp(dir=current_dir)
            # Set proper permissions to ensure we can delete it later
            os.chmod(temp_dir, 0o777)
            # Register cleanup function to run on normal program termination
            atexit.register(cleanup_temp_dir)
            clone_repo(source, temp_dir)
            path = temp_dir
            repo_name = f"{user}/{repo}"
        else:
            # Local directory
            path = str(pathlib.Path(source).resolve())
            repo_name = os.path.basename(path)
        
        # Scan directory
        files_info, total_files, total_dirs = scan_directory(
            path, max_file_size, ignore_patterns, include_patterns
        )
        
        # Create tree structure
        tree = create_tree_structure(path, files_info)
        
        # Create content string
        content = ""
        separator = "=" * 48 + "\n"
        
        # Add README.md first if it exists
        for file in files_info:
            if file['path'].lower().endswith('readme.md'):
                content += f"{separator}File: {file['path']}\n{separator}{file['content']}\n\n"
                break
                
        # Add other files
        for file in files_info:
            if not file['path'].lower().endswith('readme.md'):
                content += f"{separator}File: {file['path']}\n{separator}{file['content']}\n\n"
        
        # Create summary
        summary = (
            f"Repository: {repo_name}\n"
            f"Files analyzed: {total_files}\n"
            f"Estimated tokens: {estimate_tokens(tree + content)}\n"
        )
        
        # Write output if specified
        if output:
            with open(output, 'w', encoding='utf-8', errors='replace') as f:
                f.write(f"{summary}\n\n{tree}\n\n{content}")
                
        return summary, tree, content
        
    except Exception as e:
        if temp_dir and os.path.exists(temp_dir):
            cleanup_temp_dir()
        raise e
        
    finally:
        # Clean up temporary directory if created
        cleanup_temp_dir()

def main():
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate a text digest from a directory or GitHub repository"
    )
    parser.add_argument("source", help="Local directory path or GitHub repository URL")
    parser.add_argument("-o", "--output", default="digest.txt", help="Output file path")
    parser.add_argument("-s", "--max-size", type=int, default=1024*1024, 
                      help="Maximum file size in bytes")
    parser.add_argument("-i", "--include", help="Patterns to include (comma-separated)")
    parser.add_argument("-e", "--exclude", help="Patterns to exclude (comma-separated)")
    
    args = parser.parse_args()
    
    include_patterns = args.include.split(',') if args.include else None
    ignore_patterns = (args.exclude.split(',') if args.exclude else []) + DEFAULT_IGNORE_PATTERNS
    
    try:
        summary, _, _ = ingest(
            args.source,
            args.output,
            args.max_size,
            ignore_patterns,
            include_patterns
        )
        print(f"\nAnalysis complete! Output written to: {args.output}")
        print("\nSummary:")
        print(summary)
        
    except Exception as e:
        print(f"Error: {str(e)}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
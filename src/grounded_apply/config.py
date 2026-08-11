"""Runtime path resolution and safe creation of private local directories."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


APP_DIR_NAME = "grounded-apply"
HOME_ENV_VAR = "GROUNDED_APPLY_HOME"


class UnsafeRuntimePathError(ValueError):
    """Runtime data would be stored inside the public source repository."""


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """All filesystem locations used by a single Grounded Apply installation."""

    config_dir: Path
    data_dir: Path
    cache_dir: Path
    state_dir: Path
    portable_root: Path | None = None

    @property
    def database(self) -> Path:
        return self.data_dir / "grounded_apply.db"

    @property
    def artifacts(self) -> Path:
        return self.data_dir / "artifacts"

    @property
    def job_snapshots(self) -> Path:
        return self.data_dir / "job-snapshots"

    @property
    def generated(self) -> Path:
        return self.data_dir / "generated"

    @property
    def browser_sessions(self) -> Path:
        return self.data_dir / "browser-sessions"

    @property
    def backups(self) -> Path:
        return self.data_dir / "backups"

    @property
    def logs(self) -> Path:
        return self.state_dir / "logs"

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"

    def private_directories(self) -> tuple[Path, ...]:
        directories = (
            self.config_dir,
            self.data_dir,
            self.cache_dir,
            self.state_dir,
            self.artifacts,
            self.job_snapshots,
            self.generated,
            self.browser_sessions,
            self.backups,
            self.logs,
        )
        return directories if self.portable_root is None else (self.portable_root, *directories)

    def ensure_private_directories(self) -> None:
        """Create runtime directories and restrict them to the current user."""

        if self.portable_root is not None:
            _ensure_dedicated_portable_root(self.portable_root)
        for directory in self.private_directories():
            if directory == self.portable_root:
                continue
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)


def _expanded_path(value: str, *, setting: str) -> Path:
    expanded = Path(value).expanduser()
    if not value.strip() or not expanded.is_absolute():
        raise UnsafeRuntimePathError(f"{setting} must be an absolute path")
    return expanded.resolve()


def _environment_path(
    env: dict[str, str],
    setting: str,
    default: Path,
) -> Path:
    value = env.get(setting)
    return _expanded_path(
        str(default) if value is None or not value.strip() else value,
        setting=setting,
    )


def resolve_runtime_paths(environ: dict[str, str] | None = None) -> RuntimePaths:
    """Resolve paths without touching the filesystem.

    ``GROUNDED_APPLY_HOME`` intentionally collapses config, data, cache, and state
    beneath one portable root. Without it, XDG-compatible locations are used.
    """

    env = os.environ if environ is None else environ
    override = env.get(HOME_ENV_VAR)
    if override:
        root = _expanded_path(override, setting=HOME_ENV_VAR)
        return RuntimePaths(
            config_dir=root / "config",
            data_dir=root / "data",
            cache_dir=root / "cache",
            state_dir=root / "state",
            portable_root=root,
        )

    user_home = Path.home()
    config_root = _environment_path(
        env, "XDG_CONFIG_HOME", user_home / ".config"
    )
    data_root = _environment_path(
        env, "XDG_DATA_HOME", user_home / ".local" / "share"
    )
    cache_root = _environment_path(env, "XDG_CACHE_HOME", user_home / ".cache")
    state_root = _environment_path(
        env, "XDG_STATE_HOME", user_home / ".local" / "state"
    )
    return RuntimePaths(
        config_dir=config_root / APP_DIR_NAME,
        data_dir=data_root / APP_DIR_NAME,
        cache_dir=cache_root / APP_DIR_NAME,
        state_dir=state_root / APP_DIR_NAME,
    )


def source_checkout_root() -> Path | None:
    """Return the current source checkout root, or ``None`` when installed."""

    candidate = Path(__file__).resolve().parents[2]
    if (candidate / ".git").exists() and (candidate / "pyproject.toml").is_file():
        return candidate
    return None


def _enclosing_git_worktree(path: Path) -> Path | None:
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _reserved_portable_roots() -> frozenset[Path]:
    user_home = Path.home().resolve()
    return frozenset(
        {
            Path("/").resolve(),
            Path(tempfile.gettempdir()).resolve(),
            user_home,
            (user_home / ".config").resolve(),
            (user_home / ".cache").resolve(),
            (user_home / ".local" / "share").resolve(),
            (user_home / ".local" / "state").resolve(),
        }
    )


def _validate_dedicated_portable_root(root: Path) -> None:
    resolved = root.resolve()
    if resolved in _reserved_portable_roots():
        raise UnsafeRuntimePathError(
            f"{HOME_ENV_VAR} must name a dedicated application directory: {resolved}"
        )
    if resolved.exists():
        if not resolved.is_dir():
            raise UnsafeRuntimePathError(f"{HOME_ENV_VAR} is not a directory: {resolved}")
        mode = stat.S_IMODE(resolved.stat().st_mode)
        if mode & 0o077:
            raise UnsafeRuntimePathError(
                f"Existing {HOME_ENV_VAR} must already be private (0700): {resolved}"
            )


def _ensure_dedicated_portable_root(root: Path) -> None:
    _validate_dedicated_portable_root(root)
    resolved = root.resolve()
    if resolved.exists():
        return
    resolved.mkdir(mode=0o700, parents=True)
    resolved.chmod(0o700)


def require_runtime_outside_repository(
    paths: RuntimePaths,
    repository_root: Path | None = None,
) -> None:
    """Refuse any private runtime root beneath a detected source checkout."""

    if paths.portable_root is not None:
        _validate_dedicated_portable_root(paths.portable_root)
    runtime_roots = (paths.config_dir, paths.data_dir, paths.cache_dir, paths.state_dir)
    if repository_root is not None:
        repository_roots = {repository_root.resolve()}
    else:
        repository_roots = {
            root
            for path in runtime_roots
            if (root := _enclosing_git_worktree(path.resolve())) is not None
        }
        checkout = source_checkout_root()
        if checkout is not None:
            repository_roots.add(checkout)
    unsafe = tuple(
        path.resolve()
        for path in runtime_roots
        if any(
            path.resolve() == root or path.resolve().is_relative_to(root)
            for root in repository_roots
        )
    )
    if unsafe:
        rendered = ", ".join(str(path) for path in unsafe)
        roots = ", ".join(str(path) for path in sorted(repository_roots))
        raise UnsafeRuntimePathError(
            f"Private runtime paths must be outside Git worktrees ({roots}): {rendered}"
        )

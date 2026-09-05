"""Runtime path resolution and safe creation of private local directories."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


APP_DIR_NAME = "grounded-apply"
HOME_ENV_VAR = "GROUNDED_APPLY_HOME"
DEFAULT_CONFIG = """# Grounded Apply local configuration.
# Personal data belongs under the runtime data directory, never in this repository.

[privacy]
telemetry = false

[automation]
allow_submission = false
visible_browser = true
"""
_SQLITE_SIDECAR_SUFFIXES = ("-journal", "-wal", "-shm")


class UnsafeRuntimePathError(ValueError):
    """Runtime data would use an unsafe location, type, or permission mode."""


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
            _validate_portable_children(self)
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


def _validate_portable_children(paths: RuntimePaths) -> None:
    root = paths.portable_root
    if root is None:
        return
    try:
        resolved_root = root.resolve()
        resolved_children = tuple(
            directory.resolve()
            for directory in paths.private_directories()
            if directory != root
        )
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError(
            f"{HOME_ENV_VAR} child directories could not be resolved safely"
        ) from error
    if any(
        child == resolved_root or not child.is_relative_to(resolved_root)
        for child in resolved_children
    ):
        raise UnsafeRuntimePathError(
            f"Every {HOME_ENV_VAR} child directory must resolve beneath its private root"
        )


def _require_private_existing_path(
    path: Path,
    *,
    label: str,
    directory: bool,
) -> None:
    try:
        metadata = path.stat()
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError(
            f"Initialized {label} is not safely accessible"
        ) from error

    expected_kind = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected_kind(metadata.st_mode):
        kind = "directory" if directory else "regular file"
        raise UnsafeRuntimePathError(f"Initialized {label} must be a {kind}")
    if not directory:
        _require_single_link(metadata, label=label)
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise UnsafeRuntimePathError(
            f"Initialized {label} must already be private with no group or other access"
        )


def _require_single_link(metadata: os.stat_result, *, label: str) -> None:
    if metadata.st_nlink != 1:
        raise UnsafeRuntimePathError(
            f"Existing {label} must have exactly one hard link"
        )


def _require_existing_database_link_safety(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError(
            "Private database link count could not be validated safely"
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeRuntimePathError(
            "Existing profile database must be a regular non-symlink file"
        )
    _require_single_link(metadata, label="profile database")
    return True


def _require_existing_sqlite_sidecar_safety(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError(
            "Private database sidecar metadata could not be validated safely"
        ) from error
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeRuntimePathError(
            "Existing profile database sidecar must be a regular non-symlink file"
        )
    _require_single_link(metadata, label="profile database sidecar")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise UnsafeRuntimePathError(
            "Existing profile database sidecar must already be private with no group "
            "or other access"
        )
    return True


def _require_existing_sqlite_sidecars_safe(
    database_path: Path,
    *,
    resolved_database_path: Path,
    database_exists: bool,
) -> None:
    database_spellings = dict.fromkeys((database_path, resolved_database_path))
    sidecar_exists = False
    for candidate in database_spellings:
        for suffix in _SQLITE_SIDECAR_SUFFIXES:
            sidecar_exists = (
                _require_existing_sqlite_sidecar_safety(Path(f"{candidate}{suffix}"))
                or sidecar_exists
            )
    if sidecar_exists and not database_exists:
        raise UnsafeRuntimePathError(
            "SQLite sidecar files cannot exist without the main profile database"
        )


def _require_sqlite_sidecars_absent_for_read_only(
    database_path: Path,
    *,
    resolved_database_path: Path,
) -> None:
    database_spellings = dict.fromkeys((database_path, resolved_database_path))
    for candidate in database_spellings:
        for suffix in _SQLITE_SIDECAR_SUFFIXES:
            sidecar = Path(f"{candidate}{suffix}")
            try:
                sidecar.lstat()
            except FileNotFoundError:
                continue
            except (OSError, RuntimeError) as error:
                raise UnsafeRuntimePathError(
                    "Read-only profile storage sidecars could not be checked safely"
                ) from error
            raise UnsafeRuntimePathError(
                "Read-only profile access requires SQLite sidecar files to be absent"
            )


def _require_rollback_journal_mode_for_read_only(path: Path) -> None:
    try:
        before = path.stat()
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
            os,
            "O_NONBLOCK",
            0,
        )
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
                or opened.st_nlink != 1
                or not stat.S_ISREG(opened.st_mode)
            ):
                raise UnsafeRuntimePathError(
                    "Read-only profile database identity changed during validation"
                )
            header = os.read(descriptor, 20)
            after = os.fstat(descriptor)
            if (
                (after.st_dev, after.st_ino) != (opened.st_dev, opened.st_ino)
                or after.st_nlink != 1
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or after.st_ctime_ns != opened.st_ctime_ns
            ):
                raise UnsafeRuntimePathError(
                    "Read-only profile database changed during validation"
                )
        finally:
            os.close(descriptor)
        current = path.stat()
    except UnsafeRuntimePathError:
        raise
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError(
            "Read-only profile database header could not be validated safely"
        ) from error
    if (
        (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino)
        or current.st_nlink != 1
        or current.st_size != before.st_size
        or current.st_mtime_ns != before.st_mtime_ns
        or current.st_ctime_ns != before.st_ctime_ns
    ):
        raise UnsafeRuntimePathError(
            "Read-only profile database identity changed during validation"
        )
    if (
        len(header) >= 20
        and header[:16] == b"SQLite format 3\x00"
        and (header[18] == 2 or header[19] == 2)
    ):
        raise UnsafeRuntimePathError(
            "Read-only profile access does not support persistent SQLite WAL mode"
        )


def require_runtime_outside_repository(
    paths: RuntimePaths,
    repository_root: Path | None = None,
) -> None:
    """Refuse private runtime paths that escape into an unsafe location."""

    if paths.portable_root is not None:
        _validate_dedicated_portable_root(paths.portable_root)
        _validate_portable_children(paths)
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

    try:
        database = paths.database.resolve()
        data_dir = paths.data_dir.resolve()
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError(
            "Private database path could not be resolved safely"
        ) from error

    database_worktree = _enclosing_git_worktree(database)
    database_in_known_repository = any(
        database == root or database.is_relative_to(root) for root in repository_roots
    )
    if database_worktree is not None or database_in_known_repository:
        raise UnsafeRuntimePathError(
            "Private database path must be outside Git worktrees"
        )
    if database != data_dir and not database.is_relative_to(data_dir):
        raise UnsafeRuntimePathError(
            "Private database path must resolve within its private data directory"
        )
    database_exists = _require_existing_database_link_safety(paths.database)
    _require_existing_sqlite_sidecars_safe(
        paths.database,
        resolved_database_path=database,
        database_exists=database_exists,
    )


def require_initialized_profile_storage(
    paths: RuntimePaths,
    repository_root: Path | None = None,
    *,
    read_only: bool = False,
) -> None:
    """Validate initialized profile storage and privacy without changing it.

    Profile initialization deliberately does not call this function: its job is
    to create missing paths and repair their modes. Existing-data workflows use
    this stricter check so permission drift is never repaired as a side effect of
    an import or read-only review.
    """

    if not isinstance(read_only, bool):
        raise TypeError("read_only must be a boolean")
    require_runtime_outside_repository(paths, repository_root)
    _require_private_existing_path(
        paths.data_dir,
        label="profile data directory",
        directory=True,
    )
    _require_private_existing_path(
        paths.database,
        label="profile database",
        directory=False,
    )
    if read_only:
        _require_sqlite_sidecars_absent_for_read_only(
            paths.database,
            resolved_database_path=paths.database.resolve(),
        )
        _require_rollback_journal_mode_for_read_only(paths.database)


def require_safe_sqlite_path(
    database: Path,
    *,
    read_only: bool = False,
    require_existing: bool = True,
    allow_mode_repair: bool = False,
) -> tuple[int, int] | None:
    """Validate an adapter's filesystem target without opening it with SQLite.

    The CLI additionally validates its complete RuntimePaths configuration.
    This lower boundary covers any direct adapter path: private parent, Git
    exclusion, direct single-link files, sidecars, and read-only journal state.
    The returned identity is a sample, not a same-UID filesystem lock.
    """

    if any(type(flag) is not bool for flag in (read_only, require_existing, allow_mode_repair)):
        raise TypeError("SQLite path policy flags must be booleans")
    if read_only and allow_mode_repair:
        raise ValueError("Read-only SQLite paths cannot repair permissions")
    try:
        resolved = database.resolve()
        if _enclosing_git_worktree(resolved) is not None:
            raise UnsafeRuntimePathError("Private database path must be outside Git worktrees")
        checkout = source_checkout_root()
        if checkout is not None and resolved.is_relative_to(checkout):
            raise UnsafeRuntimePathError("Private database path must be outside Git worktrees")
        _require_private_existing_path(database.parent, label="database directory", directory=True)
        exists = _require_existing_database_link_safety(database)
        _require_existing_sqlite_sidecars_safe(
            database, resolved_database_path=resolved, database_exists=exists,
        )
        if not exists:
            if require_existing:
                raise FileNotFoundError("An existing private database is required")
            return None
        if not allow_mode_repair:
            _require_private_existing_path(database, label="profile database", directory=False)
        if read_only:
            _require_sqlite_sidecars_absent_for_read_only(database, resolved_database_path=resolved)
            _require_rollback_journal_mode_for_read_only(database)
        metadata = database.lstat()
        return metadata.st_dev, metadata.st_ino
    except UnsafeRuntimePathError:
        raise
    except FileNotFoundError:
        raise
    except (OSError, RuntimeError) as error:
        raise UnsafeRuntimePathError("Private database path could not be validated safely") from error


def prepare_sqlite_path(
    database: Path,
    *,
    existing_only: bool,
    read_only: bool,
) -> tuple[int, int]:
    """Prepare a private adapter file before SQLite can create journals.

    Initialization alone may create the database or repair its permissions,
    using a no-follow descriptor. Existing-only and read-only opens never do.
    """

    identity = require_safe_sqlite_path(
        database, read_only=read_only, require_existing=existing_only,
        allow_mode_repair=not existing_only,
    )
    if not existing_only:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_NONBLOCK"):
            raise UnsafeRuntimePathError("Secure SQLite file opening is unavailable on this platform")
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        if identity is None:
            flags |= os.O_CREAT | os.O_EXCL
        try:
            descriptor = os.open(database, flags, 0o600)
        except FileExistsError:
            # Another initializer won exclusive creation. Validate its file;
            # never truncate or overwrite it, and let SQLite serialize schema.
            identity = require_safe_sqlite_path(database)
        else:
            try:
                opened = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_nlink != 1
                    or (identity is not None and (opened.st_dev, opened.st_ino) != identity)
                ):
                    raise UnsafeRuntimePathError("Database identity changed during preparation")
                identity = (opened.st_dev, opened.st_ino)
                os.fchmod(descriptor, 0o600)
            finally:
                os.close(descriptor)
    after = require_safe_sqlite_path(database, read_only=read_only)
    if identity is None or after != identity:
        raise UnsafeRuntimePathError("Database identity changed during preparation")
    return identity

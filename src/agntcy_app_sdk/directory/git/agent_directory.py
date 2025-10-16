# Copyright AGNTCY Contributors (https://github.com/agntcy)
# SPDX-License-Identifier: Apache-2.0

import json
import hashlib
import asyncio
import shutil
from pathlib import Path
from typing import Optional, Any, Dict, List, Union
from git import Repo, GitCommandError, Actor
from contextlib import asynccontextmanager

from agntcy_app_sdk.common.logging_config import configure_logging, get_logger
from agntcy_app_sdk.directory.base import BaseAgentDirectory, RecordVisibility

configure_logging()
logger = get_logger(__name__)


class GitAgentDirectory(BaseAgentDirectory):
    """
    Git-backed implementation of the Agent Directory.

    Each holder has a folder under `agents/<holder_id>/` with visibility-scoped
    subfolders: private, protected, public.

    Repo layout:
        <repo_root>/
        └── agents/
            └── <holder_id>/
                ├── private/
                │   └── <record_id>.json
                ├── protected/
                │   └── <record_id>.json
                └── public/
                    └── <record_id>.json

    Notes:
    - Private records are not pushed by default.
    - Protected/public records may be pushed when `auto_push` is enabled.
    - All git blocking calls are executed via `asyncio.to_thread`.
    """

    # Class constants
    AGENTS_DIR = "agents"
    SIGNATURES_DIR = ".signatures"
    DEFAULT_BRANCH = "main"
    GITKEEP_FILE = ".gitkeep"

    VISIBILITY_ORDER = [
        RecordVisibility.PRIVATE,
        RecordVisibility.PROTECTED,
        RecordVisibility.PUBLIC,
    ]

    def __init__(
        self,
        repo_path: str,
        holder_id: str,
        remote_url: Optional[str] = None,
        auto_push: bool = True,
    ):
        """
        Initialize GitAgentDirectory.

        Args:
            repo_path: Local path for git repository
            holder_id: Unique identifier for this holder
            remote_url: Optional remote git URL
            auto_push: Whether to automatically push changes
        """
        # Validate inputs
        if not holder_id or not holder_id.strip():
            raise ValueError("holder_id cannot be empty")
        if not repo_path or not repo_path.strip():
            raise ValueError("repo_path cannot be empty")

        self.repo_path = Path(repo_path).resolve()
        self.holder_id = holder_id.strip()
        self.remote_url = remote_url
        self.auto_push = auto_push

        # Initialize repository
        self.repo = self._initialize_repository()
        self._setup_remote()
        self._ensure_main_branch()
        self._init_holder_directories()

    # -------------------------
    # Initialization Methods
    # -------------------------

    def _initialize_repository(self) -> Repo:
        """Initialize or open Git repository."""
        if self.remote_url and not (self.repo_path / ".git").exists():
            return self._clone_repository()
        elif (self.repo_path / ".git").exists():
            logger.info("📂 Using existing local repo at %s", self.repo_path)
            return Repo(self.repo_path)
        else:
            return self._create_new_repository()

    def _clone_repository(self) -> Repo:
        """Clone repository from remote."""
        logger.info("📦 Cloning repo from %s into %s", self.remote_url, self.repo_path)
        try:
            return Repo.clone_from(self.remote_url, self.repo_path)
        except Exception as e:
            logger.error("❌ Failed to clone remote repo: %s", e)
            raise

    def _create_new_repository(self) -> Repo:
        """Create new local repository with initial commit."""
        logger.info("🆕 Initializing new local repo at %s", self.repo_path)
        self.repo_path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(self.repo_path)

        # Create initial commit
        gitkeep = self.repo_path / self.GITKEEP_FILE
        gitkeep.write_text("")
        repo.index.add([str(gitkeep.relative_to(self.repo_path))])
        repo.index.commit("Initial commit")
        return repo

    def _setup_remote(self):
        """Configure remote origin if needed."""
        if not self.remote_url:
            return

        try:
            if "origin" not in [r.name for r in self.repo.remotes]:
                self.repo.create_remote("origin", self.remote_url)
                logger.info("🔗 Added remote origin: %s", self.remote_url)
            else:
                logger.debug("✅ Remote 'origin' already configured.")
        except Exception as e:
            logger.warning("⚠️ Failed to verify remote: %s", e)

    def _ensure_main_branch(self):
        """Ensure repository is on main branch."""
        try:
            if self.repo.active_branch.name != self.DEFAULT_BRANCH:
                self._checkout_or_create_main()
        except TypeError:
            # Detached HEAD case
            self._checkout_or_create_main()

    def _checkout_or_create_main(self):
        """Checkout main branch or create it if it doesn't exist."""
        if self.DEFAULT_BRANCH in self.repo.heads:
            self.repo.head.reference = self.repo.heads[self.DEFAULT_BRANCH]
            self.repo.head.reset(index=True, working_tree=True)
        else:
            self.repo.git.checkout("-b", self.DEFAULT_BRANCH)

    def _init_holder_directories(self):
        """Create the holder/visibility directory structure if missing."""
        self._ensure_holder_base()
        holder_path = self._holder_base()
        for vis in self.VISIBILITY_ORDER:
            (holder_path / vis.value).mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Store API
    # -------------------------

    async def push_agent_record(
        self,
        record: Union[Dict, Any],
        visibility: RecordVisibility = RecordVisibility.PUBLIC,
        *args,
        **kwargs,
    ) -> str:
        """
        Write or update a holder's record under the requested visibility.

        Args:
            record: Record data (dict or object)
            visibility: RecordVisibility level (PRIVATE|PROTECTED|PUBLIC)

        Returns:
            str: The record ID
        """
        record_id = self._extract_record_id(record)
        record_str = self._serialize_record(record)
        path = self._get_record_path(record_id, visibility)

        push_allowed = self.auto_push and (visibility != RecordVisibility.PRIVATE)

        async with self._git_transaction(
            f"Add/Update {record_id} ({visibility.value})", push_allowed
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(record_str)

        return record_id

    async def pull_agent_record(self, ref: Any, *args, **kwargs) -> Dict:
        """
        Pull an agent record for the given ID.

        Args:
            ref: Record identifier

        Returns:
            Dict: The record data

        Raises:
            FileNotFoundError: If record not found
        """
        record_id = str(ref)

        if self.remote_url:
            await self._sync()

        result = self._find_record_path(record_id)
        if result is None:
            raise FileNotFoundError(f"Record {record_id} not found")

        path, _ = result
        return self._deserialize_record(path.read_text())

    async def delete_agent_record(self, ref: Any, *args, **kwargs):
        """
        Delete an agent record across all visibility levels.

        Args:
            ref: Record identifier

        Raises:
            FileNotFoundError: If record not found
        """
        record_id = str(ref)
        paths_to_remove = []

        # Find and delete from all visibility levels
        for visibility in self.VISIBILITY_ORDER:
            path = self._get_record_path(record_id, visibility)
            if path.exists():
                path.unlink()
                paths_to_remove.append(str(path.relative_to(self.repo_path)))
                self._cleanup_empty_parent(path)

        if not paths_to_remove:
            raise FileNotFoundError(f"Record {record_id} not found")

        # Git operations
        await self._remove_from_index(paths_to_remove)
        await self._commit_and_push(
            f"Delete record {record_id}",
            push_allowed=bool(self.remote_url and self.auto_push),
        )

    # -------------------------
    # List / Search
    # -------------------------

    async def list_agent_records(self, *args, **kwargs) -> List[Dict]:
        """
        Return visible records based on holder permissions.

        Returns:
            List[Dict]: List of records with metadata
        """
        if self.remote_url:
            await self._sync()

        records = []
        agents_root = self.repo_path / self.AGENTS_DIR

        if not agents_root.exists():
            return records

        for holder_dir in agents_root.iterdir():
            if not holder_dir.is_dir():
                continue

            holder_id = holder_dir.name
            is_owner = holder_id == self.holder_id

            for visibility in self.VISIBILITY_ORDER:
                if not self._can_access(visibility, is_owner):
                    continue

                records.extend(
                    self._load_records_from_dir(holder_dir, holder_id, visibility)
                )

        return records

    async def search_agent_records(
        self, query: Union[Dict, str], limit: int = 1, *args, **kwargs
    ) -> List[Dict]:
        """
        Search records by dict filter or substring in JSON content.

        Args:
            query: Dict of key-value pairs or search string

        Returns:
            List[Dict]: Matching records
        """
        all_records = await self.list_agent_records(*args, **kwargs)

        if isinstance(query, dict):
            all_records = [
                rec
                for rec in all_records
                if all(rec.get(k) == v for k, v in query.items())
            ]
        elif isinstance(query, str):
            q = query.lower()
            all_records = [rec for rec in all_records if q in json.dumps(rec).lower()]

        if len(all_records) > limit:
            return all_records[:limit]
        return all_records

    # -------------------------
    # Signing / Verification
    # -------------------------

    async def sign_agent_record(self, record_ref: Any, provider: Any, *args, **kwargs):
        """
        Sign a record by writing a signature file.

        Args:
            record_ref: Record identifier
            provider: Signing provider identifier
        """
        record_id = str(record_ref)

        # Ensure record exists
        await self.pull_agent_record(record_id)

        sig_dir = (
            self.repo_path / self.AGENTS_DIR / self.holder_id / self.SIGNATURES_DIR
        )
        sig_dir.mkdir(parents=True, exist_ok=True)

        signature = {
            "record_id": record_id,
            "provider": str(provider),
            "signed_at": (
                self.repo.head.commit.committed_datetime.isoformat()
                if self.repo.head.commit
                else None
            ),
            "commit_sha": (
                self.repo.head.commit.hexsha if self.repo.head.commit else None
            ),
        }

        sig_path = sig_dir / f"{record_id}.sig.json"
        sig_path.write_text(json.dumps(signature, indent=2))

        # Stage and commit signature file
        try:
            await asyncio.to_thread(
                self.repo.index.add, [str(sig_path.relative_to(self.repo_path))]
            )
            await asyncio.to_thread(self.repo.index.commit, f"Sign record {record_id}")
        except Exception as e:
            logger.error("Failed to commit signature file: %s", e)
            raise

    async def verify_agent_record(self, record_ref: Any) -> Dict:
        """
        Verify signature file exists and record exists.

        Args:
            record_ref: Record identifier

        Returns:
            Dict: Verification result with details
        """
        record_id = str(record_ref)
        sig_path = (
            self.repo_path
            / self.AGENTS_DIR
            / self.holder_id
            / self.SIGNATURES_DIR
            / f"{record_id}.sig.json"
        )

        if not sig_path.exists():
            return {"verified": False, "reason": "No signature found"}

        signature = self._deserialize_record(sig_path.read_text())

        try:
            await self.pull_agent_record(record_id)
            return {"verified": True, "signature": signature, "record_exists": True}
        except FileNotFoundError:
            return {
                "verified": False,
                "reason": "Record not found",
                "signature": signature,
            }

    # -------------------------
    # Visibility API
    # -------------------------

    async def get_record_visibility(
        self, ref: Any, *args, **kwargs
    ) -> RecordVisibility:
        """
        Return the visibility of a record.

        Args:
            ref: Record identifier

        Returns:
            RecordVisibility: The visibility level

        Raises:
            FileNotFoundError: If record not found
        """
        record_id = str(ref)

        if self.remote_url:
            await self._sync()

        result = self._find_record_path(record_id)
        if result is None:
            raise FileNotFoundError(f"Record {record_id} not found")

        return result[1]

    async def set_record_visibility(
        self, ref: Any, visibility: RecordVisibility, *args, **kwargs
    ) -> bool:
        """
        Move a record between visibility levels.

        Args:
            ref: Record identifier
            visibility: Target visibility level

        Returns:
            bool: True if successful

        Raises:
            FileNotFoundError: If record not found
        """
        record_id = str(ref)

        # Find current visibility
        current = await self.get_record_visibility(record_id)

        if current == visibility:
            return True

        # Move file
        src = self._get_record_path(record_id, current)
        dst = self._get_record_path(record_id, visibility)

        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))

        # Git index operations
        try:
            await asyncio.to_thread(
                self.repo.index.remove, [str(src.relative_to(self.repo_path))]
            )
        except Exception as e:
            logger.debug("Index remove may fail if not staged: %s", e)

        await asyncio.to_thread(
            self.repo.index.add, [str(dst.relative_to(self.repo_path))]
        )

        await self._commit_and_push(
            f"Change visibility {record_id}: {current.value} -> {visibility.value}",
            push_allowed=bool(self.remote_url and self.auto_push),
        )

        return True

    # -------------------------
    # Path Helpers
    # -------------------------

    def _holder_base(self) -> Path:
        """Return base path for this holder."""
        return self.repo_path / self.AGENTS_DIR / self.holder_id

    def _ensure_holder_base(self):
        """Ensure holder base directories exist."""
        (self.repo_path / self.AGENTS_DIR).mkdir(parents=True, exist_ok=True)
        self._holder_base().mkdir(parents=True, exist_ok=True)

    def _get_record_path(
        self,
        record_id: str,
        visibility: RecordVisibility,
        holder_id: Optional[str] = None,
    ) -> Path:
        """
        Return path for a record.

        Args:
            record_id: Record identifier
            visibility: Visibility level
            holder_id: Optional holder ID (uses self.holder_id if not provided)

        Returns:
            Path: Full path to record file
        """
        holder = holder_id or self.holder_id
        base = self.repo_path / self.AGENTS_DIR / holder / visibility.value
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{record_id}.json"

    def _find_record_path(
        self, record_id: str
    ) -> Optional[tuple[Path, RecordVisibility]]:
        """
        Find record path and its visibility level.

        Args:
            record_id: Record identifier

        Returns:
            Optional tuple of (Path, RecordVisibility) or None if not found
        """
        for visibility in self.VISIBILITY_ORDER:
            path = self._get_record_path(record_id, visibility)
            if path.exists():
                return path, visibility
        return None

    # -------------------------
    # Access Control Helpers
    # -------------------------

    def _can_access(self, visibility: RecordVisibility, is_owner: bool) -> bool:
        """Check if current holder can access records with given visibility."""
        return is_owner or visibility != RecordVisibility.PRIVATE

    def _load_records_from_dir(
        self, holder_dir: Path, holder_id: str, visibility: RecordVisibility
    ) -> List[Dict]:
        """
        Load all records from a visibility directory.

        Args:
            holder_dir: Holder directory path
            holder_id: Holder identifier
            visibility: Visibility level

        Returns:
            List[Dict]: List of records with metadata
        """
        vis_dir = holder_dir / visibility.value
        if not vis_dir.exists():
            return []

        records = []
        for file in vis_dir.glob("*.json"):
            try:
                rec = self._deserialize_record(file.read_text())
                rec.update(
                    {
                        "_id": file.stem,
                        "_holder": holder_id,
                        "_visibility": visibility.value,
                    }
                )
                records.append(rec)
            except Exception as e:
                logger.warning("Failed to read record %s: %s", file, e)

        return records

    def _cleanup_empty_parent(self, path: Path):
        """Remove parent directory if empty."""
        try:
            if not any(path.parent.iterdir()):
                path.parent.rmdir()
        except (OSError, ValueError):
            pass  # Directory not empty or other error

    # -------------------------
    # Serialization Helpers
    # -------------------------

    def _extract_record_id(self, record: Any) -> str:
        """
        Extract or generate a unique ID from a record.

        Args:
            record: Record data

        Returns:
            str: Record identifier
        """
        # Try common ID attributes
        for attr in ["id", "record_id", "_id"]:
            if isinstance(record, dict) and attr in record:
                return str(record[attr])
            elif hasattr(record, attr):
                return str(getattr(record, attr))

        # Generate hash-based ID
        content = self._serialize_record(record)
        return hashlib.sha256(content.encode()).hexdigest()[:16]

    def _serialize_record(self, record: Any) -> str:
        """Serialize record to stable JSON string."""
        data = self._to_dict(record)
        return json.dumps(data, indent=2, sort_keys=True)

    def _to_dict(self, record: Any) -> Dict:
        """Convert record to dictionary."""
        if isinstance(record, dict):
            return record
        elif hasattr(record, "dict") and callable(record.dict):
            return record.dict()
        elif hasattr(record, "model_dump") and callable(record.model_dump):
            # Pydantic v2
            return record.model_dump()
        elif hasattr(record, "__dict__"):
            return record.__dict__
        else:
            return {"data": str(record)}

    def _deserialize_record(self, content: str) -> Dict:
        """Deserialize JSON content to dictionary."""
        return json.loads(content)

    # -------------------------
    # Git Helpers
    # -------------------------

    @asynccontextmanager
    async def _git_transaction(self, commit_message: str, push_allowed: bool = False):
        """
        Context manager for atomic git operations.

        Args:
            commit_message: Commit message
            push_allowed: Whether to push after commit
        """
        try:
            yield
            await self._commit_and_push(commit_message, push_allowed)
        except Exception as e:
            logger.error("Git transaction failed: %s", e)
            raise

    async def _sync(self):
        """Pull latest changes from remote."""
        if not self.remote_url:
            return

        try:
            origin = self.repo.remote("origin")
            branch = self._get_or_create_branch()

            try:
                await asyncio.to_thread(origin.pull, branch.name, rebase=True)
                logger.info("✅ Synced with remote")
            except GitCommandError as e:
                self._handle_pull_error(e)
        except Exception as e:
            logger.warning("Sync failed: %s", e)

    def _get_or_create_branch(self):
        """Get active branch or create main branch."""
        try:
            return self.repo.active_branch
        except TypeError:
            # Detached HEAD
            if self.DEFAULT_BRANCH in self.repo.heads:
                branch = self.repo.heads[self.DEFAULT_BRANCH]
            else:
                branch = self.repo.create_head(self.DEFAULT_BRANCH)
            self.repo.head.reference = branch
            self.repo.head.reset(index=True, working_tree=True)
            logger.info("Switched to branch '%s'", self.DEFAULT_BRANCH)
            return branch

    def _handle_pull_error(self, error: GitCommandError):
        """Handle common pull errors gracefully."""
        msg = str(error)
        if "Already up to date" in msg or "already up-to-date" in msg:
            logger.info("Local repository is already up to date.")
        elif "Couldn't find remote ref" in msg or "couldn't find remote ref" in msg:
            logger.info("Remote branch does not exist yet; skipping pull.")
        elif "no tracking information" in msg:
            logger.info("No tracking information; skipping pull.")
        else:
            logger.warning("Pull failed: %s", error)

    async def _safe_push(self):
        """
        Safely push to remote with proper upstream and rebase handling.

        Raises:
            ValueError: If push fails due to diverged remote
        """
        if not self.remote_url:
            logger.debug("No remote configured; skipping push.")
            return

        if "origin" not in [r.name for r in self.repo.remotes]:
            logger.warning("No remote 'origin' configured — skipping push.")
            return

        origin = self.repo.remote("origin")
        branch = self._get_or_create_branch()

        # Fetch remote
        try:
            logger.info("🔄 Fetching from remote: %s", self.remote_url)
            await asyncio.to_thread(origin.fetch)
        except Exception as e:
            logger.warning("Fetch failed: %s", e)

        # Pull changes with rebase
        try:
            logger.info(
                "⬇️ Pulling latest changes from origin/%s (rebase)...", branch.name
            )
            await asyncio.to_thread(origin.pull, branch.name, rebase=True)
            logger.info("✅ Pull + rebase completed successfully.")
        except GitCommandError as e:
            logger.warning("Pull failed before push: %s", e)
            # Attempt to abort rebase if it exists
            try:
                await asyncio.to_thread(self.repo.git.rebase, "--abort")
                logger.info("Rebase aborted after pull failure.")
            except Exception:
                pass
        except Exception as e:
            logger.warning("Pull failed before push: %s", e)

        # Push changes
        try:
            if branch.tracking_branch() is None:
                logger.info(
                    "🧭 No tracking branch for '%s' — setting upstream to origin/%s",
                    branch.name,
                    branch.name,
                )
                await asyncio.to_thread(
                    origin.push,
                    refspec=f"{branch.name}:{branch.name}",
                    force_with_lease=True,
                )
            else:
                logger.info("🚀 Pushing branch '%s' to origin...", branch.name)
                await asyncio.to_thread(origin.push)

            logger.info("✅ Push successful.")
        except GitCommandError as e:
            if "non-fast-forward" in str(e).lower():
                logger.error("❌ Push failed: remote has diverged (non-fast-forward).")
                raise ValueError(
                    "Push failed: remote has diverged. Please call sync() first."
                ) from e
            logger.error("❌ Push failed: %s", e)
            raise

    async def _commit_and_push(self, message: str, push_allowed: bool):
        """
        Commit staged changes and optionally push to remote.

        Args:
            message: Commit message
            push_allowed: Whether to push after commit
        """
        # Stage everything
        try:
            await asyncio.to_thread(self.repo.git.add, all=True)
        except Exception as e:
            logger.warning("Git add failed: %s", e)

        # Log status
        status = self.repo.git.status("--short")
        if status:
            logger.info("Git status after add:\n%s", status)

        # Commit if there are changes
        try:
            if self.repo.is_dirty(untracked_files=True):
                actor = Actor("GitAgentDirectory", "agent@directory.local")
                await asyncio.to_thread(self.repo.index.commit, message, author=actor)
                logger.info("Committed: %s", message)
            else:
                logger.info("No changes to commit.")
                return
        except Exception as e:
            logger.error("Commit failed: %s", e)
            raise

        # Push if allowed
        if push_allowed and self.remote_url and self.auto_push:
            await self._safe_push()

    async def _remove_from_index(self, paths: List[str]):
        """Remove paths from git index."""
        if not paths:
            return
        try:
            await asyncio.to_thread(self.repo.index.remove, paths)
        except Exception as e:
            logger.debug("Index remove may fail if not staged: %s", e)

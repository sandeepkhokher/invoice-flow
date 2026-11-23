"""Where invoice files live (data-protection §4).

Files are stored under `INVOICE_FLOW_DATA_DIR/files/`, outside any web-served directory, under
a generated random name; the uploader's file name is kept in the database for display only and
never touches the file system. Nothing serves this directory: a file reaches a browser only
through an authorised endpoint (S11's document viewer).
"""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.intake.files import EXTENSIONS, FileType

STORED_NAME_LENGTH = 32  # hex characters of randomness


class UnsafeStoreLocation(RuntimeError):
    """The configured store is inside a served directory."""


class FileStore:
    def __init__(self, root: Path, *, served_dirs: tuple[Path, ...] = ()) -> None:
        root = root.resolve()
        for served in served_dirs:
            served = served.resolve()
            if root == served or served in root.parents:
                raise UnsafeStoreLocation("the file store must be outside every served directory")
        self.root = root

    def _ensure_root(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def path(self, stored_name: str) -> Path:
        """The path of a stored file. Only generated names are accepted."""
        stem, _, ext = stored_name.partition(".")
        if len(stem) != STORED_NAME_LENGTH or not all(c in "0123456789abcdef" for c in stem):
            raise ValueError("not a stored file name")
        if "." + ext not in EXTENSIONS.values():
            raise ValueError("not a stored file name")
        return self.root / stored_name

    def save(self, stream: BinaryIO, file_type: FileType) -> str:
        """Copy `stream` into the store under a new name; returns that name.

        Written to a temporary file in the same directory, flushed to disk, then renamed, so
        a crash never leaves a half-written file under a stored name.
        """
        self._ensure_root()
        name = secrets.token_hex(STORED_NAME_LENGTH // 2) + EXTENSIONS[file_type]
        stream.seek(0)
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".incoming-")
        try:
            with os.fdopen(fd, "wb") as out:
                shutil.copyfileobj(stream, out)
                out.flush()
                os.fsync(out.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.root / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return name

    def delete(self, stored_name: str) -> None:
        """Remove a file whose invoice was never committed (cleanup after a failed intake)."""
        self.path(stored_name).unlink(missing_ok=True)

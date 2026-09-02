"""Dropbox content hashing — block-based SHA-256 compatible with the Dropbox API."""

import hashlib
import logging
from typing import BinaryIO

logger = logging.getLogger("orchestrator.dropbox.hashing")

BLOCK_SIZE = 4 * 1024 * 1024  # 4 MiB


def content_hash_bytes(data: bytes) -> str:
    """Compute the Dropbox content_hash of *data*, returned as lowercase hex."""
    hasher = ContentHasher()
    hasher.update(data)
    return hasher.hexdigest()


def content_hash_file(path: str) -> str:
    """Compute the Dropbox content_hash of a file, streaming in O(4 MiB) memory."""
    hasher = ContentHasher()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(BLOCK_SIZE)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


class ContentHasher:
    """Incremental Dropbox content hasher; chunk sizes fed to update() are arbitrary."""

    def __init__(self) -> None:
        self._block_hashes: list[bytes] = []
        self._current = hashlib.sha256()
        self._current_size = 0

    def update(self, chunk: bytes) -> None:
        """Feed arbitrary-length data into the hasher."""
        offset = 0
        while offset < len(chunk):
            remaining = BLOCK_SIZE - self._current_size
            end = offset + remaining
            self._current.update(chunk[offset:end])
            consumed = min(remaining, len(chunk) - offset)
            self._current_size += consumed
            offset += consumed
            if self._current_size == BLOCK_SIZE:
                self._block_hashes.append(self._current.digest())
                self._current = hashlib.sha256()
                self._current_size = 0

    def hexdigest(self) -> str:
        """Return the final Dropbox content_hash as lowercase hex."""
        # Flush any partial block
        digests = list(self._block_hashes)
        if self._current_size > 0 or not digests:
            digests.append(self._current.digest())
        overall = hashlib.sha256(b"".join(digests))
        return overall.hexdigest()

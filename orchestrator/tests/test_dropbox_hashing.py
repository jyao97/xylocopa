"""Tests for dropbox_sync.hashing — Dropbox content hash algorithm."""

import hashlib
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dropbox_sync.hashing import BLOCK_SIZE, ContentHasher, content_hash_bytes, content_hash_file


def _reference_hash(data: bytes) -> str:
    """Independent re-implementation of the Dropbox content_hash algorithm."""
    block_hashes = []
    offset = 0
    while offset < len(data):
        end = min(offset + BLOCK_SIZE, len(data))
        block_hashes.append(hashlib.sha256(data[offset:end]).digest())
        offset = end
    if not block_hashes:
        block_hashes.append(hashlib.sha256(b"").digest())
    return hashlib.sha256(b"".join(block_hashes)).hexdigest()


class TestContentHashBytes:
    """Test content_hash_bytes against the reference implementation."""

    def test_empty(self):
        data = b""
        assert content_hash_bytes(data) == _reference_hash(data)

    def test_one_byte(self):
        data = b"\x42"
        assert content_hash_bytes(data) == _reference_hash(data)

    def test_exactly_4mib(self):
        data = b"\xAB" * BLOCK_SIZE
        assert content_hash_bytes(data) == _reference_hash(data)

    def test_4mib_plus_one(self):
        data = b"\xCD" * (BLOCK_SIZE + 1)
        assert content_hash_bytes(data) == _reference_hash(data)

    def test_9mib(self):
        data = b"\xEF" * (9 * 1024 * 1024)
        assert content_hash_bytes(data) == _reference_hash(data)


class TestContentHashFile:
    """Test content_hash_file against the reference implementation."""

    def test_empty_file(self, tmp_path):
        p = tmp_path / "empty"
        p.write_bytes(b"")
        assert content_hash_file(str(p)) == _reference_hash(b"")

    def test_small_file(self, tmp_path):
        data = os.urandom(1024)
        p = tmp_path / "small"
        p.write_bytes(data)
        assert content_hash_file(str(p)) == _reference_hash(data)

    def test_multi_block_file(self, tmp_path):
        data = os.urandom(BLOCK_SIZE + 500)
        p = tmp_path / "multi"
        p.write_bytes(data)
        assert content_hash_file(str(p)) == _reference_hash(data)


class TestContentHasher:
    """Test incremental ContentHasher with irregular chunk sizes."""

    def test_irregular_chunks_equal_content_hash_bytes(self):
        data = os.urandom(BLOCK_SIZE * 2 + 1234)
        # Feed in irregular chunk sizes
        hasher = ContentHasher()
        chunk_sizes = [1, 7, 13, 1023, 4096, 65537, BLOCK_SIZE, 100]
        offset = 0
        for size in chunk_sizes:
            end = min(offset + size, len(data))
            if offset >= len(data):
                break
            hasher.update(data[offset:end])
            offset = end
        if offset < len(data):
            hasher.update(data[offset:])
        assert hasher.hexdigest() == content_hash_bytes(data)

    def test_single_byte_chunks(self):
        data = b"hello world"
        hasher = ContentHasher()
        for b in data:
            hasher.update(bytes([b]))
        assert hasher.hexdigest() == content_hash_bytes(data)

    def test_empty_updates(self):
        data = b"test data"
        hasher = ContentHasher()
        hasher.update(b"")
        hasher.update(data)
        hasher.update(b"")
        assert hasher.hexdigest() == content_hash_bytes(data)

    def test_exactly_one_block_in_two_halves(self):
        data = b"\x55" * BLOCK_SIZE
        hasher = ContentHasher()
        half = BLOCK_SIZE // 2
        hasher.update(data[:half])
        hasher.update(data[half:])
        assert hasher.hexdigest() == content_hash_bytes(data)


class TestHashProperties:
    """Verify hash properties and consistency."""

    def test_different_data_different_hash(self):
        assert content_hash_bytes(b"aaa") != content_hash_bytes(b"bbb")

    def test_hash_is_lowercase_hex_64_chars(self):
        h = content_hash_bytes(b"test")
        assert len(h) == 64
        assert h == h.lower()
        assert all(c in "0123456789abcdef" for c in h)

    def test_empty_hash_is_deterministic(self):
        assert content_hash_bytes(b"") == content_hash_bytes(b"")

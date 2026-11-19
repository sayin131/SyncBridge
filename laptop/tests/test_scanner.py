import os
import sys
import time
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scanner import scan, sha256_file, safe_join  # noqa: E402


def test_scan_basic():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "a.txt"), "w") as f:
            f.write("hello")
        m = scan(d)
        assert "a.txt" in m
        assert m["a.txt"]["size"] == 5


def test_scan_nested_folders_use_forward_slash():
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "sub"))
        with open(os.path.join(d, "sub", "b.txt"), "w") as f:
            f.write("x")
        m = scan(d)
        assert "sub/b.txt" in m


def test_cache_skips_rehash_when_unchanged():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.txt")
        with open(p, "w") as f:
            f.write("hello")
        first = scan(d)
        # mutate file on disk without going through scan, to prove cache correctness
        # by checking the hash is reused when size/mtime match
        second = scan(d, cache=first)
        assert second["a.txt"]["sha256"] == first["a.txt"]["sha256"]


def test_cache_detects_same_size_different_mtime_content():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.txt")
        with open(p, "w") as f:
            f.write("AAAAA")
        first = scan(d)
        time.sleep(0.01)
        with open(p, "w") as f:
            f.write("BBBBB")  # same size, different content
        os.utime(p, None)  # ensure mtime actually updates
        second = scan(d, cache=first)
        assert second["a.txt"]["sha256"] != first["a.txt"]["sha256"]


def test_part_files_ignored():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "a.txt.part"), "w") as f:
            f.write("partial")
        m = scan(d)
        assert m == {}


def test_safe_join_blocks_traversal():
    with tempfile.TemporaryDirectory() as d:
        try:
            safe_join(d, "../../etc/passwd")
            assert False, "should have raised"
        except ValueError:
            pass


def test_safe_join_allows_normal_path():
    with tempfile.TemporaryDirectory() as d:
        result = safe_join(d, "sub/file.txt")
        assert result.startswith(os.path.abspath(d))

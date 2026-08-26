"""Tests for the diff subcommand and _print_file_diff."""

import pytest

from retread.__main__ import _print_file_diff


class TestPrintFileDiff:
    """Tests for _print_file_diff output."""

    def test_text_both_present_different(self, capsys):
        upstream = b"line1\nline2\nline3\n"
        downstream = b"line1\nmodified\nline3\n"
        _print_file_diff("foo.py", upstream, downstream, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "--- upstream/foo.py" in out
        assert "+++ downstream/foo.py" in out
        assert "-line2" in out
        assert "+modified" in out

    def test_text_both_present_identical(self, capsys):
        data = b"same content\n"
        _print_file_diff("foo.py", data, data, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "Files are identical." in out

    def test_only_in_upstream_text(self, capsys):
        upstream = b"hello\nworld\n"
        _print_file_diff("foo.py", upstream, None, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "Only in upstream: upstream" in out
        assert "hello" in out
        assert "world" in out

    def test_only_in_downstream_text(self, capsys):
        downstream = b"new file\n"
        _print_file_diff("foo.py", None, downstream, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "Only in downstream: downstream" in out
        assert "new file" in out

    def test_binary_both_present_different(self, capsys):
        upstream = b"\x00\x01\x02\xff"
        downstream = b"\x00\x01\x03\xff"
        _print_file_diff("data.bin", upstream, downstream, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "Binary files differ." in out

    def test_not_found_either_side(self, capsys):
        _print_file_diff("missing.py", None, None, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "File not found in either wheel." in out

    def test_only_in_upstream_binary(self, capsys):
        upstream = b"\x00\xff\xfe"
        _print_file_diff("data.bin", upstream, None, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "Only in upstream: upstream" in out
        assert "Binary file." in out

    def test_only_in_downstream_binary(self, capsys):
        downstream = b"\x00\xff\xfe"
        _print_file_diff("data.bin", None, downstream, "upstream", "downstream")
        out = capsys.readouterr().out
        assert "Only in downstream: downstream" in out
        assert "Binary file." in out


class TestDiffCLIParsing:
    """Tests for diff subcommand argument parsing."""

    def test_diff_help(self):
        from retread.__main__ import main

        with pytest.raises(SystemExit, match="0"):
            main(["diff", "--help"])

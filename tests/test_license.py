"""Missing license files must not be confused with unchanged relocations."""

import zlib

import pytest

from retread import Classification, Context, PackagePolicy, Severity, VersionPolicy

from .conftest import FakeInfo, make_record, run_compare


def comparison(upstream: dict[str, bytes], downstream: dict[str, bytes], context=None):
    up_record = make_record({name: len(data) for name, data in upstream.items()})
    down_record = make_record({name: len(data) for name, data in downstream.items()})
    record_path = "foo-1.0.dist-info/RECORD"
    upstream = {**upstream, record_path: up_record}
    downstream = {**downstream, record_path: down_record}

    def infos(files):
        return {
            name: FakeInfo(name, crc=zlib.crc32(data), file_size=len(data))
            for name, data in files.items()
        }

    result = run_compare(
        upstream_infos=infos(upstream),
        downstream_infos=infos(downstream),
        upstream_record=up_record,
        downstream_record=down_record,
        context=context,
    )
    assert not result.analysis.record_mismatches
    return result


@pytest.mark.parametrize("name", ["LICENSE", "LICENCE.txt", "COPYING.LESSER", "NOTICE"])
@pytest.mark.parametrize("directory", ["foo-1.0.dist-info", "foo-1.0.dist-info/licenses"])
def test_missing_license_is_an_error(name, directory):
    path = f"{directory}/{name}"
    result = comparison({path: b"license text"}, {})
    assert result.has_errors
    (entry,) = result.analysis.only_upstream
    assert entry.filename == path
    assert entry.severity is Severity.ERROR
    assert entry.classification is Classification.LICENSE


@pytest.mark.parametrize("name", ["LICENSE", "LICENCE.txt", "COPYING.LESSER", "NOTICE"])
@pytest.mark.parametrize("reverse", [False, True])
def test_unchanged_license_relocation_is_a_notice(name, reverse):
    old, new = f"foo/{name}", f"foo-1.0.dist-info/licenses/{name}"
    if reverse:
        old, new = new, old
    result = comparison({old: b"license text"}, {new: b"license text"})
    assert not result.has_errors
    assert not result.is_identical
    assert {e.filename for e in result.analysis.only_upstream} == {old}
    assert {e.filename for e in result.analysis.only_downstream} == {new}
    assert all(
        entry.severity is Severity.NOTICE and entry.classification is Classification.LICENSE
        for entry in (*result.analysis.only_upstream, *result.analysis.only_downstream)
    )


@pytest.mark.parametrize(
    ("old", "new", "new_content"),
    [
        ("foo/LICENSE", "foo-1.0.dist-info/licenses/LICENSE", b"other text!!"),
        ("foo/LICENSE", "foo-1.0.dist-info/licenses/NOTICE", b"license text"),
        ("foo/data.txt", "foo-1.0.dist-info/licenses/data.txt", b"license text"),
        ("foo/LICENSE", "foo/other/LICENSE", b"license text"),
    ],
)
def test_unproven_relocation_still_errors(old, new, new_content):
    result = comparison({old: b"license text"}, {new: new_content})
    assert result.has_errors
    (entry,) = result.analysis.only_upstream
    assert entry.filename == old
    assert entry.severity is Severity.ERROR


@pytest.mark.parametrize("legacy_source", ["asource", "zsource"])
@pytest.mark.parametrize("legacy_destination", ["apkg", "zpkg"])
def test_license_pairing_preserves_all_copies_regardless_of_path_order(
    legacy_source, legacy_destination
):
    modern = "foo-1.0.dist-info/licenses"
    upstream = {
        f"{modern}/vendor-a/LICENSE": b"license text",
        f"{legacy_source}/LICENSE": b"license text",
    }
    downstream = {
        f"{modern}/vendor-b/LICENSE": b"license text",
        f"{legacy_destination}/LICENSE": b"license text",
    }
    result = comparison(upstream, downstream)
    assert not result.has_errors
    assert {entry.filename for entry in result.analysis.only_upstream} == set(upstream)
    assert {entry.filename for entry in result.analysis.only_downstream} == set(downstream)
    assert all(
        entry.severity is Severity.NOTICE and entry.classification is Classification.LICENSE
        for entry in (*result.analysis.only_upstream, *result.analysis.only_downstream)
    )


@pytest.mark.parametrize(
    "first_source", ["foo/LICENSE", "foo-1.0.dist-info/licenses/vendor-a/LICENSE"]
)
def test_one_relocated_license_does_not_hide_two_missing_copies(first_source):
    result = comparison(
        {first_source: b"license text", "bar/LICENSE": b"license text"},
        {"foo-1.0.dist-info/licenses/LICENSE": b"license text"},
    )
    assert result.has_errors
    assert sorted(entry.severity.value for entry in result.analysis.only_upstream) == [
        "error",
        "notice",
    ]
    (entry,) = result.analysis.only_downstream
    assert entry.severity is Severity.NOTICE
    assert entry.classification is Classification.LICENSE


def test_extra_license_is_not_a_missing_license_error():
    result = comparison({}, {"foo-1.0.dist-info/licenses/LICENSE": b"license text"})
    assert not result.has_errors
    (entry,) = result.analysis.only_downstream
    assert entry.severity is Severity.NOTICE


def test_explicit_missing_file_policy_still_applies():
    path = "foo-1.0.dist-info/licenses/LICENSE"
    policy = VersionPolicy(
        description="Explicitly accepted packaging difference",
        ignore_missing_downstream=(path,),
        ignore_differences=(),
        ignore_extra_downstream=(),
        platlib=False,
    )
    context = Context.default(
        policy={"foo": PackagePolicy(dist_name="foo", versions={"*": policy})}
    )
    result = comparison({path: b"license text"}, {}, context=context)
    assert not result.has_errors
    (entry,) = result.analysis.only_upstream
    assert entry.severity is Severity.IGNORED

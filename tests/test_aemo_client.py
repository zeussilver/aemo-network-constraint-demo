from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from src.aemo_constraint_demo import aemo_client


class DummyResponse:
    def __init__(self, text: str = "", chunks: list[bytes] | None = None) -> None:
        self.text = text
        self._chunks = chunks or []

    def __enter__(self) -> "DummyResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int) -> list[bytes]:
        return self._chunks


def _write_zip(path: Path, members: dict[str, bytes | str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            if isinstance(payload, str):
                payload = payload.encode()
            zf.writestr(name, payload)
    return path


def test_monthly_archive_urls_encode_aemo_filename() -> None:
    url = aemo_client.monthly_archive_url("dudetail", 2026, 5)
    assert "MMSDM_2026_05" in url
    assert "PUBLIC_ARCHIVE%2523DUDETAIL%2523FILE01%2523202605010000.zip" in url

    legacy = aemo_client.legacy_monthly_archive_url("DUDETAIL", 2026, 5)
    assert legacy.endswith("/PUBLIC_DVD_DUDETAIL_202605010000.zip")


def test_download_file_streams_and_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []

    def fake_get(url: str, **kwargs) -> DummyResponse:
        calls.append(url)
        return DummyResponse(chunks=[b"abc", b"", b"123"])

    monkeypatch.setattr(aemo_client.requests, "get", fake_get)
    monkeypatch.setattr(aemo_client.time, "sleep", lambda seconds: None)

    path = aemo_client.download_file("https://example.test/PUBLIC%23FILE.zip", cache_dir=tmp_path)
    assert path == tmp_path / "PUBLIC_FILE.zip"
    assert path.read_bytes() == b"abc123"

    cached = aemo_client.download_file("https://example.test/PUBLIC%23FILE.zip", cache_dir=tmp_path)
    assert cached == path
    assert calls == ["https://example.test/PUBLIC%23FILE.zip"]


def test_list_zip_links_returns_absolute_zip_urls(monkeypatch) -> None:
    html = """
    <a href="PUBLIC_A.zip">A</a>
    <a href="/Reports/CURRENT/Next_Day_Dispatch/PUBLIC_B.ZIP">B</a>
    <a href="notes.txt">ignore</a>
    """

    monkeypatch.setattr(aemo_client.requests, "get", lambda *args, **kwargs: DummyResponse(text=html))

    links = aemo_client.list_zip_links("https://www.nemweb.com.au/Reports/CURRENT/Next_Day_Dispatch/")
    assert links == [
        "https://www.nemweb.com.au/Reports/CURRENT/Next_Day_Dispatch/PUBLIC_A.zip",
        "https://www.nemweb.com.au/Reports/CURRENT/Next_Day_Dispatch/PUBLIC_B.ZIP",
    ]


def test_latest_mms_month_handles_uppercase_directory_hrefs(monkeypatch) -> None:
    responses = {
        "https://www.nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/": DummyResponse(
            text='<A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2025">2025</A>'
            '<A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2026">2026</A>'
        ),
        "https://www.nemweb.com.au/Data_Archive/Wholesale_Electricity/MMSDM/2026/": DummyResponse(
            text='<A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2026/MMSDM_2026_03">Mar</A>'
            '<A HREF="/Data_Archive/Wholesale_Electricity/MMSDM/2026/MMSDM_2026_04">Apr</A>'
        ),
    }

    monkeypatch.setattr(aemo_client.requests, "get", lambda url, **kwargs: responses[url])

    assert aemo_client.latest_mms_month() == (2026, 4)


def test_read_single_table_zip_drops_aemo_metadata_and_footer(tmp_path: Path) -> None:
    csv_text = "\n".join(
        [
            "C,MMS,DUDETAIL,1,DUID,CONNECTIONPOINTID",
            "I,MMS,DUDETAIL,1,DUID,CONNECTIONPOINTID",
            "D,MMS,DUDETAIL,1,D1,CP1",
            "C,END,OF,REPORT,,",
        ]
    )
    zip_path = _write_zip(tmp_path / "DUDETAIL.zip", {"DUDETAIL.csv": csv_text})

    df = aemo_client.read_single_table_zip(zip_path)

    assert list(df.columns) == ["DUID", "CONNECTIONPOINTID"]
    assert df.to_dict("records") == [{"DUID": "D1", "CONNECTIONPOINTID": "CP1"}]


def test_parse_multitable_zip_extracts_requested_tables_from_nested_zip(tmp_path: Path) -> None:
    csv_text = "\n".join(
        [
            "C,ignored,ignored,ignored",
            "I,DISPATCH,DISPATCHLOAD,1,SETTLEMENTDATE,DUID,TOTALCLEARED",
            "D,DISPATCH,DISPATCHLOAD,1,2026-05-01 00:05:00,D1,11.5",
            "I,DISPATCH,DISPATCHCONSTRAINT,1,SETTLEMENTDATE,CONSTRAINTID,MARGINALVALUE,VIOLATIONDEGREE,INTERVENTION",
            "D,DISPATCH,DISPATCHCONSTRAINT,1,2026-05-01 00:05:00,C1,0.4,0,0",
            "D,DISPATCH,DISPATCHCONSTRAINT,1,2026-05-01 00:10:00,C2,0,0,0",
        ]
    )
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as zf:
        zf.writestr("PUBLIC_NEXT_DAY_DISPATCH.csv", csv_text)
    zip_path = _write_zip(tmp_path / "outer.zip", {"nested.zip": inner.getvalue(), "ignore.bin": b"x"})

    parsed = aemo_client.parse_multitable_zip(zip_path, ["DISPATCHLOAD", "DISPATCHCONSTRAINT"])

    assert parsed["DISPATCHLOAD"].shape == (1, 3)
    assert parsed["DISPATCHLOAD"].loc[0, "DUID"] == "D1"
    assert parsed["DISPATCHCONSTRAINT"]["CONSTRAINTID"].tolist() == ["C1", "C2"]


def test_parse_multitable_zip_maps_current_unit_solution_to_dispatchload(tmp_path: Path) -> None:
    csv_text = "\n".join(
        [
            "I,DISPATCH,UNIT_SOLUTION,6,SETTLEMENTDATE,RUNNO,DUID,TRADETYPE,TOTALCLEARED",
            "D,DISPATCH,UNIT_SOLUTION,6,2026-05-01 00:05:00,1,D1,0,12.3",
        ]
    )
    zip_path = _write_zip(tmp_path / "dispatch.zip", {"PUBLIC_NEXT_DAY_DISPATCH.csv": csv_text})

    parsed = aemo_client.parse_multitable_zip(zip_path, ["DISPATCHLOAD"])

    assert parsed["DISPATCHLOAD"].shape == (1, 5)
    assert parsed["DISPATCHLOAD"].loc[0, "DUID"] == "D1"
    assert parsed["DISPATCHLOAD"].loc[0, "TOTALCLEARED"] == "12.3"


def test_load_next_day_dispatch_tables_concatenates_non_empty_tables(tmp_path: Path, monkeypatch) -> None:
    paths = [tmp_path / "a.zip", tmp_path / "b.zip"]
    parsed = [
        {"DISPATCHLOAD": pd.DataFrame({"DUID": ["D1"]}), "DISPATCHCONSTRAINT": pd.DataFrame()},
        {"DISPATCHLOAD": pd.DataFrame({"DUID": ["D2"]}), "DISPATCHCONSTRAINT": pd.DataFrame({"CONSTRAINTID": ["C1"]})},
    ]

    monkeypatch.setattr(aemo_client, "download_latest_next_day_dispatch", lambda **kwargs: paths)
    monkeypatch.setattr(aemo_client, "parse_multitable_zip", lambda path, table_names: parsed[paths.index(path)])

    tables = aemo_client.load_next_day_dispatch_tables(days=2, table_names=["DISPATCHLOAD", "DISPATCHCONSTRAINT"])

    assert tables["DISPATCHLOAD"]["DUID"].tolist() == ["D1", "D2"]
    assert tables["DISPATCHCONSTRAINT"]["CONSTRAINTID"].tolist() == ["C1"]


def _write_dispatch_cache(tmp_path: Path, dates: list[str]) -> list[Path]:
    return [_write_zip(tmp_path / f"PUBLIC_NEXT_DAY_DISPATCH_{date}_0001.zip", {"x.csv": "C"}) for date in dates]


def test_list_cached_next_day_dispatch_files_sorts_by_trading_date(tmp_path: Path) -> None:
    _write_dispatch_cache(tmp_path, ["20260518", "20260516", "20260517"])
    _write_zip(tmp_path / "PUBLIC_OTHER_REPORT.zip", {"x.csv": "C"})

    cached = aemo_client.list_cached_next_day_dispatch_files(tmp_path)

    assert [date.strftime("%Y%m%d") for date, _ in cached] == ["20260516", "20260517", "20260518"]


def test_next_day_dispatch_paths_prefers_cache_and_honours_end_date(tmp_path: Path, monkeypatch) -> None:
    _write_dispatch_cache(tmp_path, ["20260516", "20260517", "20260518"])

    def fail(**kwargs):
        raise AssertionError("cached windows must not hit NEMWeb")

    monkeypatch.setattr(aemo_client, "download_latest_next_day_dispatch", fail)

    paths = aemo_client.next_day_dispatch_paths(2, cache_dir=tmp_path, source="auto")
    assert [path.name[-17:-9] for path in paths] == ["20260517", "20260518"]

    bounded = aemo_client.next_day_dispatch_paths(2, cache_dir=tmp_path, source="cache", end_date="2026-05-17")
    assert [path.name[-17:-9] for path in bounded] == ["20260516", "20260517"]


def test_next_day_dispatch_paths_downloads_when_cache_is_short(tmp_path: Path, monkeypatch) -> None:
    _write_dispatch_cache(tmp_path, ["20260518"])
    downloaded = [tmp_path / "downloaded.zip"]
    monkeypatch.setattr(aemo_client, "download_latest_next_day_dispatch", lambda **kwargs: downloaded)

    assert aemo_client.next_day_dispatch_paths(3, cache_dir=tmp_path, source="auto") == downloaded

    with pytest.raises(RuntimeError, match="source='network'"):
        aemo_client.next_day_dispatch_paths(3, cache_dir=tmp_path / "empty", source="cache")


def test_find_cached_monthly_table_picks_latest_and_exact_table(tmp_path: Path) -> None:
    for name in (
        "PUBLIC_ARCHIVE%23GENCONSET%23FILE01%23202603010000.zip",
        "PUBLIC_ARCHIVE%23GENCONSET%23FILE01%23202604010000.zip",
        "PUBLIC_ARCHIVE%23GENCONSETINVOKE%23FILE01%23202605010000.zip",
    ):
        _write_zip(tmp_path / name, {"x.csv": "C"})

    found = aemo_client.find_cached_monthly_table("GENCONSET", cache_dir=tmp_path)

    assert found is not None
    assert found.name == "PUBLIC_ARCHIVE%23GENCONSET%23FILE01%23202604010000.zip"
    assert aemo_client.find_cached_monthly_table("DUDETAIL", cache_dir=tmp_path) is None


def test_read_monthly_table_cached_first_skips_the_network(tmp_path: Path, monkeypatch) -> None:
    body = "C,meta\nI,DUDETAIL,x,1,DUID\nD,DUDETAIL,x,1,CATHROCK\nC,END OF REPORT,3\n"
    csv_path = tmp_path / "PUBLIC_ARCHIVE%23DUDETAIL%23FILE01%23202604010000.zip"
    _write_zip(csv_path, {"DUDETAIL.CSV": body})

    def fail(*args, **kwargs):
        raise AssertionError("cached tables must not hit NEMWeb")

    monkeypatch.setattr(aemo_client, "read_monthly_table", fail)

    table = aemo_client.read_monthly_table_cached_first("DUDETAIL", cache_dir=tmp_path)

    assert table["DUID"].tolist() == ["CATHROCK"]

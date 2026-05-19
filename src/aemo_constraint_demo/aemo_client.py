"""Small AEMO/NEMWeb client for the network-constraint demo.

The code intentionally avoids a heavyweight data platform. It downloads only:
- small monthly standing-data tables used to map DUID -> connection point -> GENCONID;
- recent Next Day Dispatch daily ZIPs used for labels and interval grids.
"""
from __future__ import annotations

import csv
import html
import io
import re
import time
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import quote, unquote, urljoin, urlparse

import pandas as pd
import requests

NEMWEB_BASE = "https://www.nemweb.com.au"
DEFAULT_TIMEOUT = 180
HEADERS = {
    "User-Agent": "aemo-constraint-forecast-demo/0.1 (educational demo; respectful serial downloads)"
}

TABLE_ALIASES = {
    # Current Next Day Dispatch files expose the historical DISPATCHLOAD-style
    # unit dispatch solution as DISPATCH,UNIT_SOLUTION.
    "DISPATCHLOAD": ["UNIT_SOLUTION", "DISPATCH_UNIT_SOLUTION"],
}


def normalise_table_name(name: str) -> str:
    """Return a comparison-safe AEMO table name."""
    return re.sub(r"[^A-Z0-9]", "", str(name).upper())


def _safe_filename_from_url(url: str) -> str:
    parsed = urlparse(url)
    return unquote(Path(parsed.path).name).replace("#", "_")


def download_file(url: str, cache_dir: str | Path = "data/cache", overwrite: bool = False) -> Path:
    """Download a URL into cache_dir and return the local path.

    Downloads are serial and cached. Avoid hammering NEMWeb.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / _safe_filename_from_url(url)
    if out_path.exists() and not overwrite:
        return out_path

    with requests.get(url, headers=HEADERS, timeout=DEFAULT_TIMEOUT, stream=True) as response:
        response.raise_for_status()
        tmp_path = out_path.with_suffix(out_path.suffix + ".part")
        with tmp_path.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    fh.write(chunk)
        tmp_path.replace(out_path)
    time.sleep(0.15)
    return out_path


def list_zip_links(directory_url: str) -> list[str]:
    """Return absolute .zip links from a NEMWeb directory listing."""
    response = requests.get(directory_url, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    text = html.unescape(response.text)
    links = re.findall(r'href=["\']([^"\']+\.zip)["\']', text, flags=re.IGNORECASE)
    return [urljoin(directory_url, link) for link in links]


def latest_mms_month() -> tuple[int, int]:
    """Find the latest MMSDM monthly directory available on NEMWeb."""
    root = f"{NEMWEB_BASE}/Data_Archive/Wholesale_Electricity/MMSDM/"
    response = requests.get(root, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
    response.raise_for_status()
    years = sorted({int(y) for y in re.findall(r'href=["\']?(?:[^"\']*/)?(20\d{2})/?["\']?', response.text, flags=re.IGNORECASE)})
    if not years:
        raise RuntimeError("Could not find MMSDM year directories on NEMWeb.")

    for year in reversed(years):
        year_url = f"{root}{year}/"
        y_response = requests.get(year_url, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
        y_response.raise_for_status()
        months = sorted(
            {
                int(m)
                for m in re.findall(
                    rf'MMSDM_{year}_(\d{{2}})', html.unescape(y_response.text), flags=re.IGNORECASE
                )
            }
        )
        if months:
            return year, months[-1]
    raise RuntimeError("Could not find MMSDM month directories on NEMWeb.")


def monthly_archive_url(table: str, year: int, month: int, file_no: int = 1) -> str:
    """Build the post-2024 monthly MMSDM table ZIP URL.

    NEMWeb lists # in filenames, but the archive endpoint currently requires
    those characters to be double-encoded as %2523 rather than single-encoded
    as %23.
    """
    table = table.upper()
    filename = f"PUBLIC_ARCHIVE#{table}#FILE{file_no:02d}#{year}{month:02d}010000.zip"
    encoded = quote(quote(filename, safe=""), safe="")
    return (
        f"{NEMWEB_BASE}/Data_Archive/Wholesale_Electricity/MMSDM/{year}/"
        f"MMSDM_{year}_{month:02d}/MMSDM_Historical_Data_SQLLoader/DATA/{encoded}"
    )


def legacy_monthly_archive_url(table: str, year: int, month: int) -> str:
    """Build the older PUBLIC_DVD style URL used before the archive filename change."""
    table = table.upper()
    filename = f"PUBLIC_DVD_{table}_{year}{month:02d}010000.zip"
    return (
        f"{NEMWEB_BASE}/Data_Archive/Wholesale_Electricity/MMSDM/{year}/"
        f"MMSDM_{year}_{month:02d}/MMSDM_Historical_Data_SQLLoader/DATA/{filename}"
    )


def read_single_table_zip(path: str | Path) -> pd.DataFrame:
    """Read a monthly AEMO single-table ZIP into a clean DataFrame.

    Monthly MMSDM files normally contain one CSV with:
      row 1: control metadata,
      row 2: information/header row,
      rows 3..n-1: data rows,
      final row: END OF REPORT.

    We drop the 4 metadata columns that prefix both header and data rows.
    """
    path = Path(path)
    df = pd.read_csv(path, header=1, dtype=str, low_memory=False)
    if len(df) == 0:
        return pd.DataFrame()
    df = df.iloc[:-1, 4:].copy()  # remove footer and metadata columns
    df = df.dropna(how="all")
    df.columns = [str(col).strip().upper() for col in df.columns]
    return df.reset_index(drop=True)


def read_monthly_table(
    table: str,
    year: int | None = None,
    month: int | None = None,
    file_no: int = 1,
    cache_dir: str | Path = "data/cache",
) -> pd.DataFrame:
    """Download and read a monthly MMSDM table.

    If year/month are omitted, the latest available MMSDM month is used.
    """
    if year is None or month is None:
        year, month = latest_mms_month()

    urls = [monthly_archive_url(table, year, month, file_no=file_no)]
    if file_no == 1:
        urls.append(legacy_monthly_archive_url(table, year, month))

    last_error: Exception | None = None
    for url in urls:
        try:
            path = download_file(url, cache_dir=cache_dir)
            return read_single_table_zip(path)
        except Exception as exc:  # noqa: BLE001 - keep fallback simple for a demo
            last_error = exc
    raise RuntimeError(f"Could not download/read monthly table {table}: {last_error}")


@dataclass(frozen=True)
class NextDayDispatchFile:
    trading_date: pd.Timestamp
    url: str


def list_next_day_dispatch_files() -> list[NextDayDispatchFile]:
    """List current rolling-window Next Day Dispatch files."""
    directory_url = f"{NEMWEB_BASE}/Reports/CURRENT/Next_Day_Dispatch/"
    urls = list_zip_links(directory_url)
    files: list[NextDayDispatchFile] = []
    for url in urls:
        filename = unquote(Path(urlparse(url).path).name)
        match = re.search(r"PUBLIC_NEXT_DAY_DISPATCH_(\d{8})(?:_|\.zip)", filename)
        if not match:
            continue
        date = pd.to_datetime(match.group(1), format="%Y%m%d")
        files.append(NextDayDispatchFile(trading_date=date, url=url))
    return sorted(files, key=lambda item: item.trading_date)


def download_latest_next_day_dispatch(
    days: int = 14,
    cache_dir: str | Path = "data/cache",
    end_date: str | None = None,
) -> list[Path]:
    """Download the latest N Next Day Dispatch files and return local paths."""
    files = list_next_day_dispatch_files()
    if end_date is not None:
        end_ts = pd.to_datetime(end_date)
        files = [item for item in files if item.trading_date <= end_ts]
    selected = files[-days:]
    if not selected:
        raise RuntimeError("No Next Day Dispatch files found in the current rolling window.")
    return [download_file(item.url, cache_dir=cache_dir) for item in selected]


def _iter_csv_payloads_from_zip_bytes(data: bytes, depth: int = 0) -> Iterable[bytes]:
    if depth > 4:
        raise RuntimeError("Nested ZIP depth exceeds safety limit.")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if name.endswith("/"):
                continue
            payload = zf.read(name)
            lower = name.lower()
            if lower.endswith(".zip"):
                yield from _iter_csv_payloads_from_zip_bytes(payload, depth=depth + 1)
            elif lower.endswith(".csv") or lower.endswith(".txt"):
                yield payload


def _iter_csv_payloads_from_zip(path: str | Path) -> Iterable[bytes]:
    yield from _iter_csv_payloads_from_zip_bytes(Path(path).read_bytes())


def _decode_payload(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    return payload.decode("utf-8", errors="replace")


def _match_wanted_table(row: list[str], wanted_by_norm: Mapping[str, str]) -> str | None:
    if len(row) < 3:
        return None
    parts = [str(cell).strip() for cell in row[1:4]]
    candidates = {
        normalise_table_name("".join(parts[:2])),
        normalise_table_name("_".join(parts[:2])),
        normalise_table_name(parts[1]),
        normalise_table_name(parts[0]),
    }
    for candidate in candidates:
        if candidate in wanted_by_norm:
            return wanted_by_norm[candidate]
    return None


def parse_multitable_zip(path: str | Path, table_names: Iterable[str]) -> dict[str, pd.DataFrame]:
    """Parse one Next Day Dispatch multi-table ZIP.

    AEMO daily files can contain multiple CSV tables concatenated together. Rows starting
    with I contain headers; rows starting with D contain data. This parser extracts only
    the requested tables.
    """
    wanted = [table.upper() for table in table_names]
    wanted_by_norm = {normalise_table_name(table): table for table in wanted}
    for table in wanted:
        for alias in TABLE_ALIASES.get(table, []):
            wanted_by_norm[normalise_table_name(alias)] = table
    frames: dict[str, list[pd.DataFrame]] = defaultdict(list)
    buffers: dict[str, list[list[str]]] = defaultdict(list)
    columns: dict[str, list[str]] = {}

    def flush(key: str) -> None:
        rows = buffers.get(key) or []
        if not rows:
            return
        cols = columns[key]
        frames[key].append(pd.DataFrame(rows, columns=cols))
        buffers[key] = []

    for payload in _iter_csv_payloads_from_zip(path):
        text = _decode_payload(payload)
        reader = csv.reader(io.StringIO(text))
        for row in reader:
            if not row:
                continue
            row_type = str(row[0]).strip().upper()
            if row_type == "C":
                continue
            key = _match_wanted_table(row, wanted_by_norm)
            if key is None:
                continue
            if row_type == "I":
                new_cols = [str(col).strip().upper() for col in row[4:]]
                if key in columns and columns[key] != new_cols:
                    flush(key)
                columns[key] = new_cols
            elif row_type == "D":
                if key not in columns:
                    # Malformed or unexpected sequence; skip this row rather than failing the demo.
                    continue
                row_data = row[4:]
                expected = len(columns[key])
                if len(row_data) < expected:
                    row_data = row_data + [""] * (expected - len(row_data))
                buffers[key].append(row_data[:expected])

    for key in list(buffers):
        flush(key)

    out: dict[str, pd.DataFrame] = {}
    for key in wanted:
        if frames.get(key):
            df = pd.concat(frames[key], ignore_index=True)
            df.columns = [str(col).strip().upper() for col in df.columns]
            out[key] = df.dropna(how="all").reset_index(drop=True)
        else:
            out[key] = pd.DataFrame()
    return out


def load_next_day_dispatch_tables(
    days: int,
    table_names: Iterable[str],
    cache_dir: str | Path = "data/cache",
    end_date: str | None = None,
) -> dict[str, pd.DataFrame]:
    """Download recent Next Day Dispatch files and return concatenated requested tables."""
    paths = download_latest_next_day_dispatch(days=days, cache_dir=cache_dir, end_date=end_date)
    chunks: dict[str, list[pd.DataFrame]] = {table.upper(): [] for table in table_names}
    for path in paths:
        parsed = parse_multitable_zip(path, table_names=chunks.keys())
        for table, df in parsed.items():
            if not df.empty:
                chunks[table].append(df)
    return {
        table: (pd.concat(parts, ignore_index=True) if parts else pd.DataFrame())
        for table, parts in chunks.items()
    }

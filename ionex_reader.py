"""
IONEX GIM Reader

Read Global Ionosphere Map (GIM) products in IONEX format and save them
as compressed NumPy .npz files.

Supported input:
- Plain-text IONEX files (.INX, .i, .txt, or extensionless text files)
- gzip-compressed IONEX files (.gz)
- Unix-compress IONEX files (.Z) when optional package `unlzw3` is installed
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import gzip
import logging
from pathlib import Path
import sys
import re
import numpy as np

LOGGER = logging.getLogger(__name__)
MISSING_VALUE = 9999


class IONEXError(RuntimeError):
    """Base exception for IONEX parsing failures."""


class UnsupportedCompressionError(IONEXError):
    """Raised when a compressed format cannot be decoded."""


class InvalidIONEXError(IONEXError):
    """Raised when an input file is not a valid supported IONEX product."""


@dataclass
class IONEXData:
    tec: np.ndarray           # (time, lat, lon), scaled to TECU
    epochs: np.ndarray        # datetime64[s]
    lat: np.ndarray
    lon: np.ndarray
    height: np.ndarray        # (time, lat), km
    exponent: int
    source_file: str

    def save_npz(self, output_path: str | Path) -> Path:
        output_path = Path(output_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        np.savez_compressed(
            output_path,
            tec=self.tec,
            epochs=self.epochs,
            lat=self.lat,
            lon=self.lon,
            height=self.height,
            exponent=np.int16(self.exponent),
            source_file=np.array(self.source_file),
        )
        return output_path


class IONEXReader:
    """Parse TEC maps from an IONEX file."""

    def read(self, file_path: str | Path) -> IONEXData:
        path = Path(file_path).expanduser().resolve()

        if not path.is_file():
            raise FileNotFoundError(f"IONEX file not found: {path}")

        lines = self._read_lines(path)
        header_end, exponent = self._parse_header(lines)
        maps = self._parse_maps(lines, header_end, exponent)

        if not maps:
            raise InvalidIONEXError(f"No TEC maps found in {path.name}.")

        return self._assemble(maps, exponent, path.name)

    @staticmethod
    def _read_lines(path: Path) -> list[str]:
        if path.suffix.lower() == ".gz":
            try:
                with gzip.open(path, "rt", encoding="ascii", errors="replace") as f:
                    return f.readlines()
            except (OSError, EOFError) as exc:
                raise InvalidIONEXError(
                    f"Invalid gzip-compressed IONEX file: {path.name}"
                ) from exc

        if path.suffix == ".Z":
            try:
                from unlzw3 import unlzw
            except ImportError as exc:
                raise UnsupportedCompressionError(
                    "Reading .Z files requires `unlzw3`. "
                    "Install it with: pip install unlzw3"
                ) from exc

            try:
                text = unlzw(path.read_bytes()).decode("ascii", errors="replace")
                return text.splitlines(keepends=True)
            except Exception as exc:
                raise InvalidIONEXError(
                    f"Could not decompress {path.name}"
                ) from exc

        try:
            return path.read_text(
                encoding="ascii", errors="replace"
            ).splitlines(keepends=True)
        except OSError as exc:
            raise IONEXError(f"Could not read {path}") from exc

    @staticmethod
    def _label(line: str) -> str:
        return line[60:].strip() if len(line) >= 60 else line.strip()

    @classmethod
    def _parse_header(cls, lines: list[str]) -> tuple[int, int]:
        exponent = 0
        ionex_seen = False

        for i, line in enumerate(lines):
            label = cls._label(line)

            if "IONEX VERSION / TYPE" in line:
                ionex_seen = True

            if label == "EXPONENT" or line.rstrip().endswith("EXPONENT"):
                try:
                    exponent = int(line[:60].split()[0])
                except (ValueError, IndexError) as exc:
                    raise InvalidIONEXError(
                        "Could not parse EXPONENT."
                    ) from exc

            if label == "END OF HEADER" or "END OF HEADER" in line:
                if not ionex_seen:
                    raise InvalidIONEXError(
                        "IONEX VERSION / TYPE record not found."
                    )
                return i + 1, exponent

        raise InvalidIONEXError("END OF HEADER record not found.")

    @classmethod
    def _parse_maps(
        cls,
        lines: list[str],
        start: int,
        exponent: int,
    ) -> list[dict]:
        maps = []
        i = start

        while i < len(lines):
            if "START OF TEC MAP" not in lines[i]:
                i += 1
                continue

            current, i = cls._parse_one_map(lines, i + 1, exponent)
            maps.append(current)

        return maps

    @classmethod
    def _parse_one_map(
        cls,
        lines: list[str],
        i: int,
        exponent: int,
    ) -> tuple[dict, int]:
        epoch = None
        rows = []

        while i < len(lines):
            line = lines[i]

            if "END OF TEC MAP" in line:
                if epoch is None or not rows:
                    raise InvalidIONEXError("Incomplete TEC map.")
                return {"epoch": epoch, "rows": rows}, i + 1

            if "EPOCH OF CURRENT MAP" in line:
                epoch = cls._parse_epoch(line)
                i += 1
                continue

            if "LAT/LON1/LON2/DLON/H" in line:
                lat, lon1, lon2, dlon, height = cls._parse_grid_record(line)
                nlon = cls._grid_count(lon1, lon2, dlon)

                values = []
                i += 1

                while i < len(lines) and len(values) < nlon:
                    value_line = lines[i]

                    if (
                        "LAT/LON1/LON2/DLON/H" in value_line
                        or "END OF TEC MAP" in value_line
                    ):
                        break

                    try:
                        values.extend(int(v) for v in value_line.split())
                    except ValueError as exc:
                        raise InvalidIONEXError(
                            "Non-integer TEC value encountered."
                        ) from exc

                    i += 1

                if len(values) < nlon:
                    raise InvalidIONEXError(
                        f"Latitude {lat}: expected {nlon} TEC values, "
                        f"found {len(values)}."
                    )

                raw = np.asarray(values[:nlon], dtype=np.int32)
                tec = raw.astype(np.float32)
                missing = raw == MISSING_VALUE
                tec *= np.float32(10.0 ** exponent)
                tec[missing] = np.nan

                lon = cls._grid_values(lon1, lon2, dlon)
                rows.append((lat, height, lon, tec))
                continue

            i += 1

        raise InvalidIONEXError("TEC map ended before END OF TEC MAP.")

    @staticmethod
    def _parse_epoch(line: str) -> datetime:
        fields = line[:60].split()
        if len(fields) < 6:
            raise InvalidIONEXError("Invalid EPOCH OF CURRENT MAP record.")

        try:
            y, m, d, hh, mm, ss = map(int, fields[:6])
            return datetime(y, m, d, hh, mm, ss)
        except ValueError as exc:
            raise InvalidIONEXError("Invalid TEC-map epoch.") from exc

    @staticmethod
    def _parse_grid_record(line: str) -> tuple[float, float, float, float, float]:
        values = re.findall(
            r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)",
            line[:60]
        )

        if len(values) < 5:
            raise InvalidIONEXError(
                f"Invalid LAT/LON1/LON2/DLON/H record: {line.rstrip()}"
            )

        try:
            lat, lon1, lon2, dlon, height = map(float, values[:5])
        except ValueError as exc:
            raise InvalidIONEXError(
                f"Invalid TEC grid definition: {line.rstrip()}"
            ) from exc

        if dlon == 0:
            raise InvalidIONEXError("DLON must not be zero.")

        return lat, lon1, lon2, dlon, height

    @staticmethod
    def _grid_count(start: float, end: float, step: float) -> int:
        count = int(round((end - start) / step)) + 1
        if count <= 0:
            raise InvalidIONEXError(
                f"Invalid grid: start={start}, end={end}, step={step}"
            )
        return count

    @classmethod
    def _grid_values(
        cls, start: float, end: float, step: float
    ) -> np.ndarray:
        count = cls._grid_count(start, end, step)
        return start + np.arange(count, dtype=np.float64) * step

    @staticmethod
    def _assemble(
        maps: list[dict],
        exponent: int,
        source_file: str,
    ) -> IONEXData:
        first_rows = maps[0]["rows"]
        lat = np.asarray([row[0] for row in first_rows], dtype=np.float64)
        lon = np.asarray(first_rows[0][2], dtype=np.float64)

        tec = np.full(
            (len(maps), lat.size, lon.size),
            np.nan,
            dtype=np.float32,
        )
        height = np.full(
            (len(maps), lat.size),
            np.nan,
            dtype=np.float32,
        )
        epochs = []

        for t, current in enumerate(maps):
            rows = current["rows"]
            current_lat = np.asarray(
                [row[0] for row in rows], dtype=np.float64
            )

            if current_lat.shape != lat.shape or not np.allclose(current_lat, lat):
                raise InvalidIONEXError(
                    "Latitude grid changes between TEC maps."
                )

            epochs.append(np.datetime64(current["epoch"], "s"))

            for j, (_, h, row_lon, row_tec) in enumerate(rows):
                if row_lon.shape != lon.shape or not np.allclose(row_lon, lon):
                    raise InvalidIONEXError(
                        "Longitude grid changes within/between TEC maps."
                    )
                tec[t, j, :] = row_tec
                height[t, j] = h

        return IONEXData(
            tec=tec,
            epochs=np.asarray(epochs, dtype="datetime64[s]"),
            lat=lat,
            lon=lon,
            height=height,
            exponent=exponent,
            source_file=source_file,
        )


def convert_file(
    input_path: str | Path,
    output_path: str | Path | None = None,
) -> Path:
    input_path = Path(input_path).expanduser().resolve()

    if output_path is None:
        base = input_path
        if base.suffix.lower() == ".gz" or base.suffix == ".Z":
            base = base.with_suffix("")
        output_path = base.with_suffix(".npz")

    data = IONEXReader().read(input_path)
    saved = data.save_npz(output_path)

    LOGGER.info(
        "Converted %s -> %s | shape=%s",
        input_path.name,
        saved.name,
        data.tec.shape,
    )
    return saved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert an IONEX GIM file to compressed NumPy .npz."
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Input IONEX file (.INX, .gz, .Z, .i, or text file).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output .npz path. Default: beside the input file.",
    )
    return parser.parse_args()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    configure_logging()
    args = parse_args()

    try:
        convert_file(args.input, args.output)
    except (FileNotFoundError, IONEXError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 1
    except KeyboardInterrupt:
        LOGGER.warning("Conversion interrupted by user.")
        return 130

    return 0


if __name__ == "__main__":
    sys.exit(main())

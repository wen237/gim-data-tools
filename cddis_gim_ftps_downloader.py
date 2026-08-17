"""
CDDIS GIM (Global Ionosphere Map) Downloader via FTPS

Download compressed IONEX Global Ionosphere Map (GIM) products from
NASA CDDIS using FTP over TLS (FTPS).

Authentication:
CDDIS FTPS supports anonymous login. The username is "anonymous" and
the user's email address is supplied as the password.

Supported analysis centers:
- CODE (COD)
- JPL  (JPL)

Supported product types:
- Final (FIN): modern long-name products and historical short-name products
- Rapid (RAP): modern long-name products

Notes:
- Historical final IONEX products commonly use the short-name convention
  (e.g. codg0010.22i.Z).
- Newer products use the IGS long-name convention
  (e.g. COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from ftplib import (
    FTP_TLS,
    error_perm,
    error_temp,
    error_proto,
)
import gzip
import logging
from pathlib import Path
import socket
import sys
import time
from typing import Literal


LOGGER = logging.getLogger(__name__)

FTPS_HOST = "gdc.cddis.eosdis.nasa.gov"
BASE_DIRECTORY = "/pub/gps/products/ionex"

PRODUCT_SPECS = {
    "COD": {
        "short_code": "cod",
        "FIN": {
            "prefix": "COD0OPSFIN",
            "cadence": "01H",
            "description": "CODE Final",
        },
        "RAP": {
            "prefix": "COD0OPSRAP",
            "cadence": "01H",
            "description": "CODE Rapid",
        },
    },
    "JPL": {
        "short_code": "jpl",
        "FIN": {
            "prefix": "JPL0OPSFIN",
            "cadence": "02H",
            "description": "JPL Final",
        },
        "RAP": {
            "prefix": "JPL0OPSRAP",
            "cadence": "02H",
            "description": "JPL Rapid",
        },
    },
}

DownloadStatus = Literal["downloaded", "skipped", "not_found", "failed"]


@dataclass(frozen=True)
class FileCandidate:
    """One possible CDDIS filename for a requested daily product."""

    filename: str
    compression: Literal["gzip", "unix-compress"]


@dataclass
class DownloadSummary:
    """Track outcomes from a multi-day download request."""

    downloaded: int = 0
    skipped: int = 0
    not_found: int = 0
    failed: int = 0


class DownloadError(RuntimeError):
    """Base exception for downloader failures."""


class FTPSConnectionError(DownloadError):
    """Raised when the FTPS connection cannot be established or restored."""


class InvalidDownloadError(DownloadError):
    """Raised when downloaded content does not match the expected file type."""


class CDDISGIMFTPSDownloader:
    """Download and validate compressed IONEX GIM files from NASA CDDIS."""

    def __init__(
        self,
        email: str,
        output_dir: str | Path,
        *,
        timeout: int = 60,
        retries: int = 3,
        retry_backoff: float = 2.0,
    ) -> None:
        if not email or "@" not in email:
            raise ValueError("A valid email address is required for anonymous FTPS login.")
        if timeout <= 0:
            raise ValueError("timeout must be greater than 0.")
        if retries < 0:
            raise ValueError("retries must be 0 or greater.")
        if retry_backoff < 0:
            raise ValueError("retry_backoff must be 0 or greater.")

        self.email = email
        self.output_dir = Path(output_dir).expanduser().resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.timeout = timeout
        self.retries = retries
        self.retry_backoff = retry_backoff

        self.ftps: FTP_TLS | None = None
        self.connect()

    def connect(self) -> None:
        """Open an FTPS connection and enable encrypted data transfers."""
        self.close(silent=True)

        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                LOGGER.info("Connecting to %s ...", FTPS_HOST)

                ftps = FTP_TLS(timeout=self.timeout)
                ftps.connect(FTPS_HOST)
                ftps.login(user="anonymous", passwd=self.email)
                ftps.prot_p()

                self.ftps = ftps
                LOGGER.info("FTPS connection established.")
                return

            except (OSError, EOFError, error_temp, error_proto) as exc:
                last_error = exc

                if attempt >= self.retries:
                    break

                delay = self.retry_backoff * (2 ** attempt)
                LOGGER.warning(
                    "FTPS connection failed (%s). Retrying in %.1f s...",
                    exc,
                    delay,
                )
                time.sleep(delay)

        raise FTPSConnectionError(
            f"Could not connect to {FTPS_HOST}: {last_error}"
        )

    def _ensure_connection(self) -> FTP_TLS:
        """Return a live FTPS connection, reconnecting if necessary."""
        if self.ftps is None:
            self.connect()

        assert self.ftps is not None

        try:
            self.ftps.voidcmd("NOOP")
        except Exception:
            LOGGER.warning("FTPS connection was lost; reconnecting.")
            self.connect()

        assert self.ftps is not None
        return self.ftps

    @staticmethod
    def _build_long_filename(
        year: int,
        day_of_year: int,
        prefix: str,
        cadence: str,
    ) -> str:
        """Build a current IGS long-format IONEX filename."""
        return (
            f"{prefix}_{year}{day_of_year:03d}0000_"
            f"01D_{cadence}_GIM.INX.gz"
        )

    @staticmethod
    def _build_short_filename(
        year: int,
        day_of_year: int,
        short_code: str,
    ) -> str:
        """Build a historical short-format final IONEX filename."""
        return f"{short_code}g{day_of_year:03d}0.{year % 100:02d}i.Z"

    @classmethod
    def _build_candidates(
        cls,
        *,
        year: int,
        day_of_year: int,
        agency: str,
        product_type: str,
    ) -> list[FileCandidate]:
        """
        Build candidate filenames in preferred order.

        Final products try the modern long-name convention first, then fall
        back to the historical short-name convention. Rapid products currently
        use only the modern long-name convention.
        """
        agency_spec = PRODUCT_SPECS[agency]
        product_spec = agency_spec[product_type]

        candidates = [
            FileCandidate(
                filename=cls._build_long_filename(
                    year,
                    day_of_year,
                    product_spec["prefix"],
                    product_spec["cadence"],
                ),
                compression="gzip",
            )
        ]

        if product_type == "FIN":
            candidates.append(
                FileCandidate(
                    filename=cls._build_short_filename(
                        year,
                        day_of_year,
                        agency_spec["short_code"],
                    ),
                    compression="unix-compress",
                )
            )

        return candidates

    @staticmethod
    def _validate_gzip_ionex(path: Path) -> None:
        """Confirm that a gzip file decompresses to a recognizable IONEX header."""
        try:
            with gzip.open(path, "rt", encoding="ascii", errors="replace") as handle:
                header = "".join(handle.readline() for _ in range(20))
        except (OSError, EOFError) as exc:
            raise InvalidDownloadError(
                f"File is not a valid gzip archive: {path.name}"
            ) from exc

        if "IONEX VERSION / TYPE" not in header:
            raise InvalidDownloadError(
                f"File does not contain a recognizable IONEX header: {path.name}"
            )

    @staticmethod
    def _validate_unix_compress(path: Path) -> None:
        """Confirm that a historical .Z file has Unix-compress magic bytes."""
        try:
            with path.open("rb") as handle:
                magic = handle.read(2)
        except OSError as exc:
            raise InvalidDownloadError(
                f"Could not read downloaded file: {path.name}"
            ) from exc

        if magic != b"\x1f\x9d":
            raise InvalidDownloadError(
                f"File is not a valid Unix-compress (.Z) archive: {path.name}"
            )

    @classmethod
    def _validate_download(
        cls,
        path: Path,
        compression: Literal["gzip", "unix-compress"],
    ) -> None:
        """Validate a downloaded file according to its compression format."""
        if not path.exists() or path.stat().st_size == 0:
            raise InvalidDownloadError(f"Downloaded file is empty: {path.name}")

        if compression == "gzip":
            cls._validate_gzip_ionex(path)
        else:
            cls._validate_unix_compress(path)

    def _change_directory(self, directory: str) -> None:
        """Change the remote FTPS working directory with reconnect retry."""
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                ftps = self._ensure_connection()
                ftps.cwd(directory)
                return

            except error_perm:
                raise

            except (OSError, EOFError, error_temp, error_proto) as exc:
                last_error = exc

                if attempt >= self.retries:
                    break

                delay = self.retry_backoff * (2 ** attempt)
                LOGGER.warning(
                    "Failed to access remote directory (%s). "
                    "Reconnecting in %.1f s...",
                    exc,
                    delay,
                )
                time.sleep(delay)
                self.connect()

        raise FTPSConnectionError(
            f"Could not access remote directory {directory}: {last_error}"
        )

    def _remote_file_exists(self, filename: str) -> bool:
        """
        Check whether a remote file exists without listing the whole directory.

        SIZE is preferred because it avoids downloading a potentially long
        directory listing. Some FTP servers may not support SIZE consistently,
        so NLST(filename) is used as a fallback.
        """
        ftps = self._ensure_connection()

        try:
            size = ftps.size(filename)
            if size is not None:
                return True
        except error_perm as exc:
            message = str(exc)
            if message.startswith("550"):
                return False
        except Exception:
            pass

        try:
            result = ftps.nlst(filename)
            return any(Path(item).name == filename for item in result)
        except error_perm as exc:
            if str(exc).startswith("550"):
                return False
            raise

    def _download_candidate(
        self,
        *,
        candidate: FileCandidate,
        temp_path: Path,
    ) -> None:
        """Download one remote file to a temporary local path."""
        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                ftps = self._ensure_connection()

                with temp_path.open("wb") as output_file:
                    ftps.retrbinary(
                        f"RETR {candidate.filename}",
                        output_file.write,
                        blocksize=64 * 1024,
                    )

                return

            except error_perm:
                raise

            except (OSError, EOFError, error_temp, error_proto, socket.timeout) as exc:
                last_error = exc
                temp_path.unlink(missing_ok=True)

                if attempt >= self.retries:
                    break

                delay = self.retry_backoff * (2 ** attempt)
                LOGGER.warning(
                    "Download interrupted for %s (%s). Retrying in %.1f s...",
                    candidate.filename,
                    exc,
                    delay,
                )
                time.sleep(delay)
                self.connect()

        raise FTPSConnectionError(
            f"Could not download {candidate.filename}: {last_error}"
        )

    def download_range(
        self,
        start_date: datetime,
        end_date: datetime,
        *,
        agency: str = "COD",
        product_type: str = "FIN",
    ) -> DownloadSummary:
        """Download one daily GIM file for each date in an inclusive range."""
        if start_date > end_date:
            raise ValueError("start_date must be earlier than or equal to end_date.")

        if agency not in PRODUCT_SPECS:
            raise ValueError(f"Unsupported agency: {agency!r}.")
        if product_type not in {"FIN", "RAP"}:
            raise ValueError(f"Unsupported product type: {product_type!r}.")

        spec = PRODUCT_SPECS[agency][product_type]

        LOGGER.info("Selected product: %s", spec["description"])
        LOGGER.info("Output directory: %s", self.output_dir)

        if agency == "JPL":
            LOGGER.info("JPL long-name products use a 2-hour cadence.")

        if product_type == "RAP":
            LOGGER.info(
                "Rapid downloads currently use the modern long-name convention only."
            )

        summary = DownloadSummary()

        total_days = (end_date.date() - start_date.date()).days + 1
        current_date = start_date

        while current_date <= end_date:
            LOGGER.info(
                "Processing %s (%d/%d)...",
                current_date.strftime("%Y-%m-%d"),
                (current_date.date() - start_date.date()).days + 1,
                total_days,
            )

            status = self._download_daily_file(
                date=current_date,
                agency=agency,
                product_type=product_type,
            )

            if status == "downloaded":
                summary.downloaded += 1
            elif status == "skipped":
                summary.skipped += 1
            elif status == "not_found":
                summary.not_found += 1
            else:
                summary.failed += 1

            current_date += timedelta(days=1)

        LOGGER.info(
            "Finished: %d downloaded, %d skipped, %d not found, %d failed.",
            summary.downloaded,
            summary.skipped,
            summary.not_found,
            summary.failed,
        )

        return summary

    def _download_daily_file(
        self,
        *,
        date: datetime,
        agency: str,
        product_type: str,
    ) -> DownloadStatus:
        """Try all valid filename conventions for one requested day."""
        year = date.year
        day_of_year = date.timetuple().tm_yday

        remote_directory = f"{BASE_DIRECTORY}/{year}/{day_of_year:03d}"

        try:
            self._change_directory(remote_directory)
        except error_perm as exc:
            if str(exc).startswith("550"):
                LOGGER.warning(
                    "Remote directory not found: %s",
                    remote_directory,
                )
                return "not_found"
            raise

        candidates = self._build_candidates(
            year=year,
            day_of_year=day_of_year,
            agency=agency,
            product_type=product_type,
        )

        for candidate in candidates:
            local_path = self.output_dir / candidate.filename

            if not local_path.exists():
                continue

            try:
                self._validate_download(local_path, candidate.compression)
            except InvalidDownloadError:
                LOGGER.warning(
                    "Existing file is invalid and will be removed: %s",
                    candidate.filename,
                )
                local_path.unlink(missing_ok=True)
            else:
                LOGGER.info("Skipping existing valid file: %s", candidate.filename)
                return "skipped"

        for candidate in candidates:
            try:
                if not self._remote_file_exists(candidate.filename):
                    continue
            except (OSError, EOFError, error_temp, error_proto) as exc:
                LOGGER.error(
                    "Could not check remote file [%s]: %s",
                    candidate.filename,
                    exc,
                )
                return "failed"

            local_path = self.output_dir / candidate.filename
            temp_path = self.output_dir / f"{candidate.filename}.part"

            temp_path.unlink(missing_ok=True)

            try:
                LOGGER.info("Downloading: %s", candidate.filename)

                self._download_candidate(
                    candidate=candidate,
                    temp_path=temp_path,
                )

                self._validate_download(
                    temp_path,
                    candidate.compression,
                )

                temp_path.replace(local_path)

                LOGGER.info("Downloaded: %s", candidate.filename)
                return "downloaded"

            except error_perm as exc:
                temp_path.unlink(missing_ok=True)

                if str(exc).startswith("550"):
                    continue

                LOGGER.error(
                    "Permission error [%s]: %s",
                    candidate.filename,
                    exc,
                )
                return "failed"

            except (FTPSConnectionError, InvalidDownloadError, OSError) as exc:
                temp_path.unlink(missing_ok=True)

                LOGGER.error(
                    "Download failed [%s]: %s",
                    candidate.filename,
                    exc,
                )
                return "failed"

        LOGGER.warning(
            "No matching file found for %s (%s %s).",
            date.strftime("%Y-%m-%d"),
            agency,
            product_type,
        )
        return "not_found"

    def close(self, *, silent: bool = False) -> None:
        """Close the FTPS connection."""
        if self.ftps is None:
            return

        try:
            self.ftps.quit()
        except Exception:
            try:
                self.ftps.close()
            except Exception:
                if not silent:
                    LOGGER.debug("Error while closing FTPS connection.", exc_info=True)
        finally:
            self.ftps = None

    def __enter__(self) -> "CDDISGIMFTPSDownloader":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def parse_date(value: str) -> datetime:
    """Parse a CLI date in YYYY-MM-DD format."""
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; expected YYYY-MM-DD."
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download NASA CDDIS GIM (IONEX) products over FTPS "
            "(CODE/JPL; Final/Rapid)."
        )
    )

    parser.add_argument(
        "--email",
        type=str,
        required=True,
        help="Email address used as the password for anonymous FTPS login.",
    )
    parser.add_argument(
        "--start-date",
        "--start_date",
        dest="start_date",
        type=parse_date,
        required=True,
        help="Start date, inclusive (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        "--end_date",
        dest="end_date",
        type=parse_date,
        required=True,
        help="End date, inclusive (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--agency",
        choices=sorted(PRODUCT_SPECS),
        default="COD",
        help="Analysis center: COD (CODE) or JPL. Default: COD.",
    )
    parser.add_argument(
        "--type",
        dest="product_type",
        choices=["FIN", "RAP"],
        default="FIN",
        help="Product type: FIN (Final) or RAP (Rapid). Default: FIN.",
    )
    parser.add_argument(
        "--output-folder",
        "--output_folder",
        dest="output_folder",
        type=Path,
        default=Path("./gim_data"),
        help="Destination directory. Default: ./gim_data",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="FTPS timeout in seconds. Default: 60.",
    )
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Retries for transient FTPS/network failures. Default: 3.",
    )

    return parser.parse_args()


def configure_logging() -> None:
    """Configure console logging for CLI use."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def main() -> int:
    configure_logging()
    args = parse_args()

    if args.start_date > args.end_date:
        LOGGER.error("--start-date must be earlier than or equal to --end-date.")
        return 2

    try:
        with CDDISGIMFTPSDownloader(
            email=args.email,
            output_dir=args.output_folder,
            timeout=args.timeout,
            retries=args.retries,
        ) as downloader:
            summary = downloader.download_range(
                start_date=args.start_date,
                end_date=args.end_date,
                agency=args.agency,
                product_type=args.product_type,
            )

    except (ValueError, FTPSConnectionError, OSError) as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.warning("Download interrupted by user.")
        return 130

    return 1 if summary.failed > 0 else 0


if __name__ == "__main__":
    sys.exit(main())

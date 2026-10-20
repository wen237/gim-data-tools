"""GIM-2 regression tests for ionex_reader.py (run from repository root)."""
import gzip
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest

import ionex_reader as reader


def ionex_text():
    def record(value, label):
        return f"{value:<60}{label}\n"
    return "".join([
        record("1.0 IONOSPHERE MAPS", "IONEX VERSION / TYPE"),
        record("-1", "EXPONENT"),
        record("", "END OF HEADER"),
        record("1", "START OF TEC MAP"),
        record("2022 1 1 0 0 0", "EPOCH OF CURRENT MAP"),
        record("0 -180 -175 5 450", "LAT/LON1/LON2/DLON/H"),
        " 100 9999\n",
        record("1", "END OF TEC MAP"),
    ])


def write_ionex(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name.lower().endswith(".gz"):
        with gzip.open(path, "wt", encoding="ascii") as f:
            f.write(ionex_text())
    else:
        path.write_text(ionex_text(), encoding="ascii")
    return path


@pytest.mark.parametrize("name", [
    "codg0010.22i.Z", "codg0010.11i.Z", "codg0010.22i",
    "COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz",
    "example.INX", "example.i", "example.txt",
])
def test_supported_names(name):
    assert reader._is_ionex_candidate(Path(name))


@pytest.mark.parametrize("name", ["notes.md", "image.png", "file.npz", "bad.Z"])
def test_unsupported_names(name):
    assert not reader._is_ionex_candidate(Path(name))


def test_year_preserved_in_batch_names():
    assert reader._output_name(Path("codg0010.22i.Z")) == "codg0010.22i.npz"
    assert reader._output_name(Path("codg0010.23i.Z")) == "codg0010.23i.npz"
    assert reader._output_name(Path("COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz")) == "COD0OPSFIN_20260010000_01D_01H_GIM.npz"


def test_real_gzip_parse_scaling_missing_and_epoch(tmp_path):
    path = write_ionex(tmp_path / "test.INX.gz")
    data = reader.IONEXReader().read(path)
    assert data.tec.shape == (1, 1, 2)
    assert data.tec[0, 0, 0] == pytest.approx(10.0)
    assert np.isnan(data.tec[0, 0, 1])
    assert data.epochs[0] == np.datetime64("2022-01-01T00:00:00")
    assert data.lon.tolist() == [-180.0, -175.0]


def test_batch_mixed_formats_and_original_unchanged(tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "out"
    gz = write_ionex(src / "COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz")
    historical = src / "codg0010.22i.Z"
    historical.write_bytes(b"synthetic-placeholder-for-.Z")
    originals = {p.name: p.read_bytes() for p in (gz, historical)}
    real_read = reader.IONEXReader.read

    def read_with_synthetic_z(self, path):
        if Path(path).suffix == ".Z":
            return real_read(self, gz)
        return real_read(self, path)

    monkeypatch.setattr(reader.IONEXReader, "read", read_with_synthetic_z)
    result = reader.convert_directory(src, out)
    assert (result.converted, result.skipped, result.failed) == (2, 0, 0)
    assert (out / "codg0010.22i.npz").is_file()
    assert (out / "COD0OPSFIN_20260010000_01D_01H_GIM.npz").is_file()
    assert all((src / name).read_bytes() == raw for name, raw in originals.items())


def test_repeat_skips_and_preserves_output(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    write_ionex(src / "day.INX.gz")
    first = reader.convert_directory(src, out)
    path = out / "day.npz"
    before = path.read_bytes()
    second = reader.convert_directory(src, out)
    assert (first.converted, first.skipped, first.failed) == (1, 0, 0)
    assert (second.converted, second.skipped, second.failed) == (0, 1, 0)
    assert path.read_bytes() == before


def test_invalid_file_isolated(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    write_ionex(src / "good.INX.gz")
    (src / "bad.INX.gz").write_bytes(b"not gzip")
    result = reader.convert_directory(src, out)
    assert (result.converted, result.skipped, result.failed) == (1, 0, 1)
    assert (out / "good.npz").is_file()
    assert not (out / "bad.npz").exists()


def test_output_name_collision_refused(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    write_ionex(src / "day.INX")
    write_ionex(src / "day.INX.gz")
    result = reader.convert_directory(src, out)
    assert (result.converted, result.skipped, result.failed) == (0, 0, 2)
    assert not (out / "day.npz").exists()


def test_different_years_do_not_collide(tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "out"
    a = write_ionex(src / "day.INX")
    for name in ("codg0010.22i.Z", "codg0010.23i.Z"):
        (src / name).write_bytes(b"placeholder")
    real_read = reader.IONEXReader.read

    def fake_z(self, path):
        return real_read(self, a) if Path(path).suffix == ".Z" else real_read(self, path)

    monkeypatch.setattr(reader.IONEXReader, "read", fake_z)
    result = reader.convert_directory(src, out)
    assert (result.converted, result.failed) == (3, 0)
    assert (out / "codg0010.22i.npz").is_file()
    assert (out / "codg0010.23i.npz").is_file()


def test_invalid_directory(tmp_path):
    with pytest.raises(NotADirectoryError):
        reader.convert_directory(tmp_path / "missing", tmp_path / "out")


def test_same_input_output_directory_rejected(tmp_path):
    with pytest.raises(ValueError, match="must differ"):
        reader.convert_directory(tmp_path, tmp_path)


def test_cli_help_and_missing_batch_output(tmp_path):
    script = Path(reader.__file__).resolve()
    help_result = subprocess.run([sys.executable, str(script), "--help"], capture_output=True, text=True)
    assert help_result.returncode == 0
    assert "--input-dir" in help_result.stdout
    missing = subprocess.run([sys.executable, str(script), "--input-dir", str(tmp_path)], capture_output=True, text=True)
    assert missing.returncode == 2


def test_cli_real_batch_success_and_repeat(tmp_path):
    script = Path(reader.__file__).resolve()
    src, out = tmp_path / "src", tmp_path / "out"
    write_ionex(src / "day.INX.gz")
    cmd = [sys.executable, str(script), "--input-dir", str(src), "--output-dir", str(out)]
    first = subprocess.run(cmd, capture_output=True, text=True)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "1 converted" in first.stdout
    second = subprocess.run(cmd, capture_output=True, text=True)
    assert second.returncode == 0
    assert "1 skipped" in second.stdout


def test_cli_real_batch_partial_failure(tmp_path):
    script = Path(reader.__file__).resolve()
    src, out = tmp_path / "src", tmp_path / "out"
    write_ionex(src / "good.INX.gz")
    (src / "bad.INX.gz").write_bytes(b"invalid")
    result = subprocess.run([sys.executable, str(script), "--input-dir", str(src), "--output-dir", str(out)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "1 converted" in result.stdout
    assert "1 failed" in result.stdout

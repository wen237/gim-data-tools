# GIM Data Tools

A lightweight Python toolkit for downloading and reading Global Ionosphere Map (GIM) products in IONEX format.

This repository provides utilities for retrieving GIM data from the NASA Crustal Dynamics Data Information System (CDDIS) archive and converting IONEX TEC maps into NumPy arrays for scientific analysis.

The tools were developed to support reproducible ionospheric total electron content (TEC) analysis and are used as data-access utilities for related research workflows.

## Features

- Download GIM products from NASA CDDIS using encrypted FTPS access.
- Anonymous FTPS authentication using an email address.
- Support for CODE and JPL GIM products.
- Support for Final (`FIN`) and Rapid (`RAP`) products.
- Support for modern gzip-compressed IONEX files (`.INX.gz`).
- Support for historical Unix-compress IONEX files (`.Z`) for Final products.
- Automatic retry and reconnection for transient FTPS/network failures.
- Validation of downloaded files before they are saved.
- Parse IONEX TEC maps without assuming a fixed number of maps or fixed grid dimensions.
- Apply the IONEX `EXPONENT` scaling factor automatically.
- Convert raw IONEX TEC values of `9999` and `0` to `NaN` before exponent scaling.
- Export TEC maps and coordinates to compressed NumPy (`.npz`) files.
- Convert a directory of IONEX files in a non-recursive batch, skipping existing outputs and rejecting ambiguous output filenames.

## Repository Structure

```text
gim-data-tools/
├── cddis_gim_ftps_downloader.py
├── ionex_reader.py
├── requirement.txt
├── tests/
│   └── test_ionex_batch.py
├── README.md
├── LICENSE
└── .gitignore
```

- `cddis_gim_ftps_downloader.py` — downloads GIM/IONEX products from NASA CDDIS.
- `ionex_reader.py` — reads IONEX TEC maps and converts them to NumPy `.npz` files.
- `requirement.txt` — Python dependencies required by the tools.
- `tests/test_ionex_batch.py` — regression tests for batch conversion.

## Requirements

- Python 3.10 or later
- Internet access with FTPS connectivity

Python dependencies:

- NumPy
- unlzw3

The `unlzw3` package is used to decompress historical Unix-compress (`.Z`) IONEX files.

## Installation

Clone the repository:

```bash
git clone https://github.com/<your-username>/gim-data-tools.git
cd gim-data-tools
```

Install the required Python packages:

```bash
python -m pip install -r requirement.txt
```

## Data Source

The downloader retrieves Global Ionosphere Map products from the NASA CDDIS GNSS archive.

The current implementation supports products from:

| Analysis Center | Code | Final | Rapid | Typical Cadence |
| --- | --- | --- | --- | --- |
| Center for Orbit Determination in Europe | `COD` | Yes | Yes | 1 hour |
| Jet Propulsion Laboratory | `JPL` | Yes | Yes | 2 hours |

Product availability and naming conventions may vary with date and analysis center.

### Authentication

The downloader accesses CDDIS through FTP over TLS (FTPS).

CDDIS FTPS supports anonymous authentication. The downloader therefore uses:

```text
Username: anonymous
Password: your email address
```

No Earthdata username or password is stored by this repository.

## Usage

### 1. Download GIM Data

The basic command is:

```bash
python cddis_gim_ftps_downloader.py \
    --email your_email@example.com \
    --start-date 2026-01-01 \
    --end-date 2026-01-03 \
    --agency COD \
    --type FIN \
    --output-folder ./gim_data
```

On Windows PowerShell, the command can be entered on one line:

```powershell
python cddis_gim_ftps_downloader.py --email your_email@example.com --start-date 2026-01-01 --end-date 2026-01-03 --agency COD --type FIN --output-folder ./gim_data
```

Available options:

| Option | Description |
| --- | --- |
| `--email` | Email address used for anonymous FTPS authentication |
| `--start-date` | First date to download (`YYYY-MM-DD`) |
| `--end-date` | Last date to download (`YYYY-MM-DD`) |
| `--agency` | Analysis center: `COD` or `JPL` |
| `--type` | Product type: `FIN` or `RAP` |
| `--output-folder` | Directory in which downloaded files are stored |
| `--timeout` | FTPS timeout in seconds |
| `--retries` | Number of retries for transient connection failures |

The date range is inclusive and may span months or years.

### Modern and Historical Files

Modern GIM products use the IGS long filename convention. For example:

```text
COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz
```

Historical Final products may use the legacy short filename convention, for example:

```text
codg0010.22i.Z
```

For Final products, the downloader first checks the modern filename and then falls back to the historical filename when necessary.

Historical fallback for Rapid products is not currently implemented.

---

### 2. Read and Convert IONEX Data

The reader can directly process a downloaded gzip-compressed IONEX file:

```bash
python ionex_reader.py gim_data/COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz
```

By default, the resulting `.npz` file is saved beside the input file.

A custom output path can also be specified:

```bash
python ionex_reader.py gim_data/COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz \
    --output processed/gim_2026001.npz
```

The reader supports:

```text
.INX
.INX.gz
.i
.Z
```

Historical `.Z` files are decompressed using `unlzw3`.

### 3. Batch Convert IONEX Files

To convert all supported IONEX files directly inside one input directory:

```powershell
python ionex_reader.py --input-dir "./gim_data" --output-dir "./processed"
```

Batch conversion is **non-recursive**: files in nested subdirectories are not scanned. Existing output files are skipped rather than overwritten. If multiple input files would produce the same output filename (case-insensitively), those inputs are reported as failures instead of being converted ambiguously. The command returns a nonzero exit status when one or more conversions fail.

The batch converter writes each completed archive via a temporary file and publishes it without overwriting an output that already exists. Input and output directories must differ.

## Output Data Structure

The generated `.npz` archive contains:

| Variable | Description |
| --- | --- |
| `tec` | TEC maps with shape `(time, latitude, longitude)` |
| `epochs` | Epoch of each TEC map |
| `lat` | Latitude coordinates in degrees |
| `lon` | Longitude coordinates in degrees |
| `height` | Ionospheric shell height in kilometers |
| `exponent` | IONEX exponent read from the source file |
| `source_file` | Original IONEX filename |

### TEC Scaling and Missing-Value Handling

TEC is stored as `float64` in **TECU**, after applying the IONEX `EXPONENT` field. Raw IONEX integer values of `9999` (missing-value sentinel) **and `0`** are treated as invalid by this reader and converted to `NaN` **before scaling**. Consequently, zero-valued raw cells are not retained as valid zero TEC observations.

For negative exponents, scaling uses division by the corresponding positive power of ten (for example, `raw / 10.0` for `EXPONENT = -1`), preserving exact numerical consistency with the validated legacy CODE GIM processing convention. Other exponent values use multiplication by the specified power of ten.

The saved `tec` array is already in TECU; **do not apply the exponent or divide by ten again** when loading the `.npz` file.

The resulting archive can be loaded with NumPy:

```python
import numpy as np

data = np.load("gim_2026001.npz")

tec = data["tec"]
epochs = data["epochs"]
lat = data["lat"]
lon = data["lon"]

print("TEC shape:", tec.shape)
print("Epochs:", epochs.shape)
print("Latitude:", lat.shape)
print("Longitude:", lon.shape)
```

TEC values can then be accessed as:

```python
tec[time_index, latitude_index, longitude_index]
```

## Example Workflow

A typical workflow is:

```text
NASA CDDIS
    │
    ▼
cddis_gim_ftps_downloader.py
    │
    ▼
IONEX GIM
(.INX.gz / .Z)
    │
    ▼
ionex_reader.py
    │
    ▼
NumPy archive
(.npz)
    │
    ▼
TEC analysis / visualization
```

For example:

```bash
python cddis_gim_ftps_downloader.py \
    --email your_email@example.com \
    --start-date 2026-01-01 \
    --end-date 2026-01-03 \
    --agency COD \
    --type FIN \
    --output-folder ./gim_data
```

Then convert a downloaded product:

```bash
python ionex_reader.py ./gim_data/COD0OPSFIN_20260010000_01D_01H_GIM.INX.gz
```

## Testing and Validation

Run the repository's tests from the project root:

```powershell
python -m pytest -q
```

If pytest is not installed in the active environment, install it separately (for example, `python -m pip install pytest`). During the GIM-3B repository review, **23 tests passed** in the contributor's local environment. Test results should be rerun after subsequent code changes.

### Legacy/Modern CODE GIM Cross-Validation

A separate scientific-framework validation compared legacy CODE GIM `.npz` inputs with `.npz` files produced by this reader for **2022-01-01 through 2022-01-16** (16 UTC days). In that specified dataset and configuration, the validation found:

- Exact TEC and `NaN` mask equality on the physical **71 × 73** grid across **384 hourly epochs**, using the first 24 maps of each day.
- Identical Framework D spatial anomaly counts and ratios for `reference_days=15`, `window_steps=24`, and `k=3.0`.
- Identical Framework A temporal input and anomaly results at **30°N, 70°E** for the specified 24-hour analysis window.
- Identical Framework B+C historical daily TEC and hourly metadata at the same grid point, with no interpolation.

These results establish **Legacy/Modern consistency for the specified inputs and analysis windows**. They do **not** constitute full historical earthquake-case reproduction or validation of multi-event Z-tests and K-means clustering. The integration validation scripts and report belong to the related scientific-framework repository rather than this data-access repository.

## Citation

If you use these tools in scientific work, please cite the associated research article and acknowledge the original GIM data providers.

## Data Attribution

This repository contains software for accessing and processing GIM products. The GIM data themselves are provided by their respective analysis centers and distributed through NASA CDDIS.

Users are responsible for following the applicable data-provider citation and usage requirements.

The downloaded data are not distributed under this repository's software license.

## License

The source code in this repository is released under the MIT License. See [LICENSE](LICENSE) for details.

The license applies to the software in this repository and does not apply to third-party GIM/IONEX data downloaded using these tools.

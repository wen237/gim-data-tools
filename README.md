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
- Convert missing TEC values (`9999`) to `NaN`.
- Export TEC maps and coordinates to compressed NumPy (`.npz`) files.

## Repository Structure

```text
gim-data-tools/
├── cddis_gim_ftps_downloader.py
├── ionex_reader.py
├── requirements.txt
├── README.md
├── LICENSE
└── .gitignore
```

- `cddis_gim_ftps_downloader.py` — downloads GIM/IONEX products from NASA CDDIS.
- `ionex_reader.py` — reads IONEX TEC maps and converts them to NumPy `.npz` files.
- `requirements.txt` — Python dependencies required by the tools.

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
python -m pip install -r requirements.txt
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

TEC values are scaled according to the `EXPONENT` field in the IONEX file.

The IONEX missing-value indicator `9999` is converted to `NaN`.

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

## Citation

If you use these tools in scientific work, please cite the associated research article and acknowledge the original GIM data providers.

## Data Attribution

This repository contains software for accessing and processing GIM products. The GIM data themselves are provided by their respective analysis centers and distributed through NASA CDDIS.

Users are responsible for following the applicable data-provider citation and usage requirements.

The downloaded data are not distributed under this repository's software license.

## License

The source code in this repository is released under the MIT License. See [LICENSE](LICENSE) for details.

The license applies to the software in this repository and does not apply to third-party GIM/IONEX data downloaded using these tools.

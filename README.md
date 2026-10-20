# Karoo2Garmin 🚴‍♂️💨 ➡️ ⌚

Automated sync service that downloads your cycling activities from **Hammerhead Karoo** (Karoo 1, Karoo 2, and Karoo 3), enhances the `.fit` files with Garmin Edge device signatures to enable Garmin **Physio TrueUp** (Training Load, Training Effect, VO2 Max, Recovery Time), and uploads them directly to **Garmin Connect**.

Packaged as a lightweight, containerized Docker service suitable for running on home servers, NAS devices (Synology, Unraid, TrueNAS), Raspberry Pis, or any system with Docker.

---

## 🌟 Key Features

* **🔄 Automated Synchronization**: Runs as a set-and-forget background daemon polling Hammerhead Cloud for new rides at configurable intervals (e.g., every 60 minutes).
* **🎯 Garmin Physio TrueUp & Training Metrics**: By default, third-party cycling computer rides uploaded to Garmin Connect do not calculate Training Load or Training Effect. **Karoo2Garmin** patches the FIT file metadata (`FileIdMessage` and `DeviceInfoMessage`) to identify as a **Garmin Edge 1040** (or Edge 840), unlocking full physiological training metrics in Garmin Connect.
* **⌚ Watch-Safe Ride Summaries**: Garmin Connect syncs a summary of each ride to your Garmin watch, built from the file's activity, session and lap messages. The Karoo writes these without fields every Garmin device includes (session count, lap indexes, events, bounding box), and opening such a ride in the watch's activity history can reboot the watch. **Karoo2Garmin** rebuilds them the way a Garmin Edge writes them.
* **🛰️ Sensor & Shifting Data Preservation**: Unlike virtual cycling converters, **Karoo2Garmin** preserves all outdoor GPS trackpoints, barometric elevation, temperatures, external ANT+/BLE sensors (heart rate, power meters, radar), and electronic gear shifting events (SRAM AXS and Shimano Di2).
* **🛡️ Multi-Tier Duplicate Prevention**:
  1. **Persistent SQLite Store**: Remembers synced activity IDs across reboots and container restarts.
  2. **Garmin Connect Time Window**: Checks Garmin Connect for activities matching within a ±2 hour window (preventing duplicates if you also recorded on a Garmin watch).
  3. **HTTP 409 Conflict Protection**: Gracefully catches Garmin's upload duplicate response.
* **🔑 Secure Token Caching**: Stores Garmin session tokens inside the `data/garmin_tokens` directory, preventing repetitive 2FA prompts and avoiding account lockouts.
* **🐳 Docker & Docker Compose Ready**: Single-command startup with minimal resource overhead and only a single volume mount (`./data:/data`). Multi-architecture image builds (amd64 and arm64).
* **🛠️ Flexible CLI Controls**: Supports single-ride sync (`--sync-latest`), batch backfills (`--batch 10`), database inspection (`--status`), and simulations (`--dry-run`).

---

## 🏗️ Architecture

```
┌─────────────────────────┐
│  Hammerhead Karoo Cloud │
│  - REST Session / OAuth │
│  - Activity Discovery   │
│  - Raw .FIT Download    │
└────────────┬────────────┘
             │ Raw .fit download
             ▼
┌─────────────────────────┐
│   FIT Device Enhancer   │
│  - fit-tool processor   │
│  - Edge 1040/840 spoof  │
│  - Preserves GPS & AXS  │
│  - NP / IF / TSS work   │
└────────────┬────────────┘
             │ Enhanced .fit
             ▼
┌─────────────────────────┐
│     Garmin Connect      │
│  - Cached Token Auth    │
│  - Duplicate Checking   │
│  - FIT Binary Upload    │
└────────────┬────────────┘
             │ Upload Confirmation
             ▼
┌─────────────────────────┐
│   SQLite Sync Store     │
│ (/data/karoo_sync_state)│
└─────────────────────────┘
```

---

## 🚀 Quick Start with Docker Compose (Recommended)

### 1. Clone the repository

```bash
git clone https://github.com/erwinvol/Karoo2Garmin.git
cd Karoo2Garmin
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` or edit `docker-compose.yml`:

```yaml
services:
  karoo2garmin:
    build: .
    image: karoo2garmin:latest
    container_name: karoo2garmin
    restart: unless-stopped
    environment:
      - KAROO_EMAIL=your_karoo_email@example.com
      - KAROO_PASSWORD=your_karoo_password
      - GARMIN_USERNAME=your_garmin_email@example.com
      - GARMIN_PASSWORD=your_garmin_password
      - DATA_DIR=/data
      - GARMIN_TOKEN_DIR=/data/garmin_tokens
      - SYNC_INTERVAL_MINUTES=60
    volumes:
      - ./data:/data
```

> [!NOTE]
> All SQLite activity tracking and Garmin session tokens are self-contained inside the `./data` volume. Only a single volume mount (`./data:/data`) is required!

### 3. Launch with Docker Compose

```bash
docker compose up -d
```

View the logs in real time:

```bash
docker compose logs -f
```

---

## 🐳 Running with Docker CLI

### Build the Image:

```bash
docker build -t karoo2garmin:latest .
```

### Run as a Background Daemon (60m interval):

```bash
docker run -d \
  --name karoo2garmin \
  --restart unless-stopped \
  -e KAROO_EMAIL="your_email@example.com" \
  -e KAROO_PASSWORD="your_password" \
  -e GARMIN_USERNAME="your_garmin_email@example.com" \
  -e GARMIN_PASSWORD="your_garmin_password" \
  -v $(pwd)/data:/data \
  karoo2garmin:latest
```

### Run a One-Off Sync (Latest Ride Only):

```bash
docker run --rm \
  -e KAROO_EMAIL="your_email@example.com" \
  -e KAROO_PASSWORD="your_password" \
  -v $(pwd)/data:/data \
  karoo2garmin:latest --sync-latest
```

### View Synchronization Status:

```bash
docker run --rm \
  -v $(pwd)/data:/data \
  karoo2garmin:latest --status
```

---

## ⚙️ Configuration Reference

All settings can be configured directly inside `docker-compose.yml`, environment variables, or a `.env` file:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `KAROO_EMAIL` | **Required\*** | Your Hammerhead Karoo login email address |
| `KAROO_PASSWORD` | **Required\*** | Your Hammerhead Karoo account password |
| `KAROO_AUTH_TOKEN` | *Optional* | Pre-existing JWT Bearer token (alternative to email/password) |
| `GARMIN_USERNAME` | *Optional* | Your Garmin Connect email (required if tokens are not yet cached) |
| `GARMIN_PASSWORD` | *Optional* | Your Garmin Connect password |
| `DATA_DIR` | `/data` | Directory where SQLite state database and tokens are stored |
| `GARMIN_TOKEN_DIR` | `/data/garmin_tokens` | Directory storing Garmin session tokens (inside `DATA_DIR`) |
| `SYNC_INTERVAL_MINUTES`| `60` | Polling frequency in minutes when in daemon mode (default 60m) |
| `SYNC_BATCH_SIZE` | `10` | Max recent activities to evaluate per cycle |
| `CHECK_DUPLICATES` | `true` | Verify if ride is already in Garmin Connect before uploading |
| `DUPLICATE_THRESHOLD_HOURS` | `2` | Matching time window around ride timestamp (prevents dual-recording dupes) |
| `SPOOF_GARMIN_DEVICE`| `true` | Patch FIT file with Garmin Edge device signatures |
| `DEVICE_PRODUCT_ID` | `3843` | Garmin Device ID (`3843` = Edge 1040, `4062` = Edge 840) |
| `DEVICE_SOFTWARE_VERSION` | `27.10` | Device firmware version reported in FIT header |
| `ATHLETE_FTP` | `0` | Optional FTP wattage for Normalized Power / TSS calculations |
| `LOG_LEVEL` | `INFO` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |

---

## 💻 Local Installation (Without Docker)

### 1. Requirements

* Python 3.10+
* pip

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Run Commands

```bash
# Check installed version
python -m src.main --version

# Sync only the single latest activity
python -m src.main --sync-latest

# Batch sync the last 10 activities
python -m src.main --batch 10

# Test run without uploading (simulation)
python -m src.main --dry-run

# Run as daemon
python -m src.main --daemon --interval 30

# Inspect sync history
python -m src.main --status
```

---

## 🧪 Running Tests

```bash
python -m unittest discover tests
```

---

## 📄 License

This project is licensed under the [MIT License](LICENSE).

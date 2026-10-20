"""Configuration loader for Karoo2Garmin service."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Config:
    """Application configuration for Karoo2Garmin."""

    # Hammerhead Karoo credentials & token
    karoo_email: str
    karoo_password: str
    karoo_auth_token: Optional[str]

    # Garmin Connect credentials & tokens
    garmin_username: Optional[str]
    garmin_password: Optional[str]
    garmin_token_dir: Path

    # Service sync parameters
    data_dir: Path
    sync_interval_minutes: int = 60
    sync_batch_size: int = 10
    check_duplicates: bool = True
    duplicate_threshold_hours: int = 2

    # FIT enhancement settings
    spoof_garmin_device: bool = True
    device_product_id: int = 3843  # Garmin Edge 1040 (default), 4062 for Edge 840
    device_software_version: float = 27.10
    athlete_ftp: float = 0.0

    # Operational settings
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Config":
        """Load configuration from environment variables."""
        # Support both KAROO_* and HAMMERHEAD_* prefixes
        karoo_email = (
            os.getenv("KAROO_EMAIL", "").strip()
            or os.getenv("HAMMERHEAD_EMAIL", "").strip()
            or os.getenv("HAMMERHEAD_USERNAME", "").strip()
        )
        karoo_password = (
            os.getenv("KAROO_PASSWORD", "").strip()
            or os.getenv("HAMMERHEAD_PASSWORD", "").strip()
        )
        karoo_auth_token = (
            os.getenv("KAROO_AUTH_TOKEN", "").strip()
            or os.getenv("HAMMERHEAD_AUTH_TOKEN", "").strip()
            or None
        )

        if not karoo_auth_token and (not karoo_email or not karoo_password):
            raise ValueError(
                "Either KAROO_AUTH_TOKEN or (KAROO_EMAIL and KAROO_PASSWORD) environment variables are required."
            )

        garmin_username = os.getenv("GARMIN_USERNAME", "").strip() or None
        garmin_password = os.getenv("GARMIN_PASSWORD", "").strip() or None

        # Resolve Data directory
        data_dir_env = os.getenv("DATA_DIR", "").strip()
        if data_dir_env:
            data_dir = Path(os.path.expanduser(data_dir_env))
        elif Path("/data").exists() and os.access("/data", os.W_OK):
            data_dir = Path("/data")
        else:
            data_dir = Path("./data")

        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            data_dir = Path("./data")
            data_dir.mkdir(parents=True, exist_ok=True)

        # Resolve Garmin token directory (defaults to inside data_dir)
        token_dir_env = os.getenv("GARMIN_TOKEN_DIR", "").strip()
        if token_dir_env:
            garmin_token_dir = Path(os.path.expanduser(token_dir_env))
        else:
            garmin_token_dir = data_dir / "garmin_tokens"

        try:
            garmin_token_dir.mkdir(parents=True, exist_ok=True)
        except (PermissionError, OSError):
            garmin_token_dir = data_dir / "garmin_tokens"
            garmin_token_dir.mkdir(parents=True, exist_ok=True)

        # Automatically copy existing host tokens from ~/.garminconnect if token dir is empty
        try:
            user_tokens_dir = Path(os.path.expanduser("~/.garminconnect"))
            if user_tokens_dir.exists() and not any(garmin_token_dir.iterdir()):
                import shutil
                for item in user_tokens_dir.iterdir():
                    if item.is_file():
                        shutil.copy2(item, garmin_token_dir / item.name)
        except Exception:
            pass

        sync_interval = int(os.getenv("SYNC_INTERVAL_MINUTES", "60"))
        sync_batch = int(os.getenv("SYNC_BATCH_SIZE", "10"))
        check_dups = os.getenv("CHECK_DUPLICATES", "true").lower() in ("true", "1", "yes")
        dup_threshold = int(os.getenv("DUPLICATE_THRESHOLD_HOURS", "2"))

        spoof_device = os.getenv("SPOOF_GARMIN_DEVICE", "true").lower() in ("true", "1", "yes")
        device_product = int(os.getenv("DEVICE_PRODUCT_ID", "3843"))
        device_sw = float(os.getenv("DEVICE_SOFTWARE_VERSION", "27.10"))
        ftp = float(os.getenv("ATHLETE_FTP", "0.0"))

        log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        return cls(
            karoo_email=karoo_email,
            karoo_password=karoo_password,
            karoo_auth_token=karoo_auth_token,
            garmin_username=garmin_username,
            garmin_password=garmin_password,
            garmin_token_dir=garmin_token_dir,
            data_dir=data_dir,
            sync_interval_minutes=sync_interval,
            sync_batch_size=sync_batch,
            check_duplicates=check_dups,
            duplicate_threshold_hours=dup_threshold,
            spoof_garmin_device=spoof_device,
            device_product_id=device_product,
            device_software_version=device_sw,
            athlete_ftp=ftp,
            log_level=log_level,
        )

"""Unit tests for Karoo2Garmin configuration."""

import os
import unittest
from unittest.mock import patch

from src.config import Config


class TestConfig(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_credentials_raises_error(self):
        with self.assertRaises(ValueError):
            Config.from_env()

    @patch.dict(
        os.environ,
        {
            "KAROO_EMAIL": "test@hammerhead.io",
            "KAROO_PASSWORD": "secretpassword",
            "DATA_DIR": "/tmp/test_karoo_data",
            "SYNC_INTERVAL_MINUTES": "30",
            "DEVICE_PRODUCT_ID": "4062",
        },
        clear=True,
    )
    def test_valid_config_loads_defaults_and_env(self):
        cfg = Config.from_env()
        self.assertEqual(cfg.karoo_email, "test@hammerhead.io")
        self.assertEqual(cfg.karoo_password, "secretpassword")
        self.assertEqual(cfg.sync_interval_minutes, 30)
        self.assertEqual(cfg.device_product_id, 4062)
        self.assertTrue(cfg.check_duplicates)
        self.assertTrue(cfg.spoof_garmin_device)

    @patch.dict(
        os.environ,
        {
            "HAMMERHEAD_EMAIL": "sram@example.com",
            "HAMMERHEAD_PASSWORD": "srampassword",
        },
        clear=True,
    )
    def test_hammerhead_prefix_fallback(self):
        cfg = Config.from_env()
        self.assertEqual(cfg.karoo_email, "sram@example.com")
        self.assertEqual(cfg.karoo_password, "srampassword")

    @patch.dict(
        os.environ,
        {
            "KAROO_AUTH_TOKEN": "header.payload.signature",
        },
        clear=True,
    )
    def test_auth_token_without_password_succeeds(self):
        cfg = Config.from_env()
        self.assertEqual(cfg.karoo_auth_token, "header.payload.signature")


if __name__ == "__main__":
    unittest.main()

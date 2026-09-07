import json
import os
import unittest
from unittest.mock import patch

from market_signal_lab.alpaca import AlpacaCredentials, AlpacaMarketDataClient


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False


class AlpacaClientTests(unittest.TestCase):
    def test_credentials_must_come_from_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "APCA_API_KEY_ID"):
                AlpacaCredentials.from_environment()

    @patch("market_signal_lab.alpaca.urlopen")
    def test_downloads_all_pages_without_putting_secrets_in_url(self, mocked_open):
        mocked_open.side_effect = [
            _FakeResponse(
                {
                    "bars": {
                        "AAPL": [
                            {
                                "t": "2026-01-02T05:00:00Z",
                                "o": 100,
                                "h": 105,
                                "l": 99,
                                "c": 104,
                                "v": 1000,
                                "n": 50,
                                "vw": 102,
                            }
                        ]
                    },
                    "next_page_token": "next-token",
                }
            ),
            _FakeResponse(
                {
                    "bars": {
                        "MSFT": [
                            {
                                "t": "2026-01-02T05:00:00Z",
                                "o": 200,
                                "h": 205,
                                "l": 198,
                                "c": 203,
                                "v": 2000,
                            }
                        ]
                    },
                    "next_page_token": None,
                }
            ),
        ]
        credentials = AlpacaCredentials("example-key", "example-secret")
        result = AlpacaMarketDataClient(credentials).fetch_daily_bars(
            ["MSFT", "AAPL"], start="2026-01-01", end="2026-01-03"
        )
        self.assertEqual(result["ticker"].tolist(), ["AAPL", "MSFT"])
        self.assertEqual(mocked_open.call_count, 2)
        first_request = mocked_open.call_args_list[0].args[0]
        second_request = mocked_open.call_args_list[1].args[0]
        self.assertNotIn("example-secret", first_request.full_url)
        self.assertIn("page_token=next-token", second_request.full_url)
        self.assertEqual(first_request.get_header("Apca-api-secret-key"), "example-secret")


if __name__ == "__main__":
    unittest.main()

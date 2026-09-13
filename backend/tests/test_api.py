import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import freshbid.api as api_module
from freshbid.service import FreshBidService


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        api_module.service = FreshBidService(Path(self.temp_directory.name) / "api.sqlite3")
        self.client = TestClient(api_module.app)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_full_browser_workflow(self) -> None:
        response = self.client.post(
            "/api/recommendations",
            json={
                "product_name": "Croissant",
                "inventory_on_hand": 36,
                "recent_sales_units": 5,
                "recent_footfall": 12,
                "original_price_cents": 2400,
                "current_price_cents": 2400,
                "approved_price_cents": [2400, 2160, 1920],
                "hours_until_close": 2,
            },
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "recommended")
        self.assertTrue(body["safety"]["passed"])
        recommendation_id = body["recommendation"]["recommendation_id"]

        for action, expected_status in (
            ("approve", "approved"),
            ("apply", "applied"),
            ("verify", "verified"),
        ):
            response = self.client.post(
                f"/api/recommendations/{recommendation_id}/{action}",
                json={"actor": "test-manager"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], expected_status)

        response = self.client.post(
            f"/api/recommendations/{recommendation_id}/outcomes",
            json={"units_sold": 11, "ending_inventory": 25, "source": "simulated"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["outcomes"][0]["units_sold"], 11)


if __name__ == "__main__":
    unittest.main()

import unittest
from unittest.mock import patch, MagicMock
from loyalty import app, compute_status


def make_db():
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


class LoyaltyTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    # ---------- pure logic ----------

    def test_compute_status_boundaries(self):
        self.assertEqual(compute_status(0), ("BRONZE", 5))
        self.assertEqual(compute_status(9), ("BRONZE", 5))
        self.assertEqual(compute_status(10), ("SILVER", 7))
        self.assertEqual(compute_status(19), ("SILVER", 7))
        self.assertEqual(compute_status(20), ("GOLD", 10))
        self.assertEqual(compute_status(100), ("GOLD", 10))

    # ---------- GET /api/v1/loyalty ----------

    def test_get_loyalty_no_header(self):
        r = self.client.get("/api/v1/loyalty")
        self.assertEqual(r.status_code, 400)

    @patch("loyalty.get_conn")
    def test_get_loyalty_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.get("/api/v1/loyalty", headers={"X-User-Name": "Unknown"})
        self.assertEqual(r.status_code, 404)

    @patch("loyalty.get_conn")
    def test_get_loyalty_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (25, "GOLD", 10)
        mock_get_conn.return_value = conn

        r = self.client.get("/api/v1/loyalty", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "GOLD")
        self.assertEqual(body["discount"], 10)
        self.assertEqual(body["reservationCount"], 25)

    # ---------- POST /api/v1/loyalty/increment ----------

    def test_increment_no_header(self):
        r = self.client.post("/api/v1/loyalty/increment")
        self.assertEqual(r.status_code, 400)

    @patch("loyalty.get_conn")
    def test_increment_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/loyalty/increment", headers={"X-User-Name": "X"})
        self.assertEqual(r.status_code, 404)

    @patch("loyalty.get_conn")
    def test_increment_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (25,)
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/loyalty/increment", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["reservationCount"], 26)
        self.assertEqual(body["status"], "GOLD")
        self.assertEqual(body["discount"], 10)

    # ---------- POST /api/v1/loyalty/decrement ----------

    def test_decrement_no_header(self):
        r = self.client.post("/api/v1/loyalty/decrement")
        self.assertEqual(r.status_code, 400)

    @patch("loyalty.get_conn")
    def test_decrement_downgrades_gold_to_silver(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (20,)
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/loyalty/decrement", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["reservationCount"], 19)
        self.assertEqual(body["status"], "SILVER")
        self.assertEqual(body["discount"], 7)

    @patch("loyalty.get_conn")
    def test_decrement_downgrades_silver_to_bronze(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (10,)
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/loyalty/decrement", headers={"X-User-Name": "Test Max"})
        body = r.get_json()
        self.assertEqual(body["reservationCount"], 9)
        self.assertEqual(body["status"], "BRONZE")
        self.assertEqual(body["discount"], 5)

    @patch("loyalty.get_conn")
    def test_decrement_not_below_zero(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (0,)
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/loyalty/decrement", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["reservationCount"], 0)

    # ---------- health ----------

    def test_health(self):
        r = self.client.get("/manage/health")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()

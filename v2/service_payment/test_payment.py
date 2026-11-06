import unittest
from unittest.mock import patch, MagicMock
from payment import app


def make_db():
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


class PaymentTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    # ---------- POST /api/v1/payments ----------

    @patch("payment.get_conn")
    def test_create_payment_ok(self, mock_get_conn):
        conn, _ = make_db()
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/payments", json={"price": 27000})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "PAID")
        self.assertEqual(body["price"], 27000)
        self.assertIn("paymentUid", body)
        self.assertEqual(len(body["paymentUid"]), 36)  # uuid4 str

    @patch("payment.get_conn")
    def test_create_payment_float_price_is_cast_to_int(self, mock_get_conn):
        conn, _ = make_db()
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/payments", json={"price": 26999.9})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["price"], 26999)

    def test_create_payment_missing_price(self):
        r = self.client.post("/api/v1/payments", json={})
        self.assertEqual(r.status_code, 400)

    def test_create_payment_negative_price(self):
        r = self.client.post("/api/v1/payments", json={"price": -1})
        self.assertEqual(r.status_code, 400)

    def test_create_payment_non_numeric_price(self):
        r = self.client.post("/api/v1/payments", json={"price": "abc"})
        self.assertEqual(r.status_code, 400)

    # ---------- GET /api/v1/payments/<uid> ----------

    @patch("payment.get_conn")
    def test_get_payment_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (
            "11111111-1111-1111-1111-111111111111", "PAID", 27000,
        )
        mock_get_conn.return_value = conn

        r = self.client.get("/api/v1/payments/11111111-1111-1111-1111-111111111111")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "PAID")
        self.assertEqual(body["price"], 27000)
        self.assertEqual(body["paymentUid"], "11111111-1111-1111-1111-111111111111")

    @patch("payment.get_conn")
    def test_get_payment_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.get("/api/v1/payments/unknown")
        self.assertEqual(r.status_code, 404)

    # ---------- POST /api/v1/payments/<uid>/cancel ----------

    @patch("payment.get_conn")
    def test_cancel_payment_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = ("PAID",)
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/payments/pay-1/cancel")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "CANCELED")

    @patch("payment.get_conn")
    def test_cancel_payment_already_canceled(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = ("CANCELED",)
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/payments/pay-1/cancel")
        self.assertEqual(r.status_code, 409)

    @patch("payment.get_conn")
    def test_cancel_payment_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.post("/api/v1/payments/pay-1/cancel")
        self.assertEqual(r.status_code, 404)

    def test_health(self):
        r = self.client.get("/manage/health")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()

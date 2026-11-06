import unittest
import uuid as _uuid
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from reservation import app


HOTEL_UID = "049161bb-badd-4fa8-9d90-87c9a82b0668"
PAYMENT_UID = "22222222-2222-2222-2222-222222222222"
RESERVATION_UID = "11111111-1111-1111-1111-111111111111"


def make_db():
    conn = MagicMock()
    cur = MagicMock()
    cur.__enter__ = MagicMock(return_value=cur)
    cur.__exit__ = MagicMock(return_value=False)
    conn.cursor.return_value = cur
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn, cur


HOTEL_ROW = (
    HOTEL_UID, "Ararat Park Hyatt Moscow",
    "Россия", "Москва", "Неглинная ул., 4", 5, 10000,
)

# (reservation_uid, status, start, end, payment_uid,
#  hotel_uid, name, country, city, address, stars, username)
RESERVATION_ROW = (
    RESERVATION_UID, "PAID",
    datetime(2021, 10, 8, tzinfo=timezone.utc),
    datetime(2021, 10, 11, tzinfo=timezone.utc),
    PAYMENT_UID,
    HOTEL_UID, "Ararat Park Hyatt Moscow",
    "Россия", "Москва", "Неглинная ул., 4", 5,
    "Test Max",
)


class ReservationTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    # ---------- GET /api/v1/hotels ----------

    @patch("reservation.get_conn")
    def test_list_hotels_default_pagination(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (1,)          # COUNT(*)
        cur.fetchall.return_value = [HOTEL_ROW]
        mock_get_conn.return_value = conn

        r = self.client.get("/api/v1/hotels")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["page"], 1)
        self.assertEqual(body["pageSize"], 1)
        self.assertEqual(body["totalElements"], 1)
        self.assertEqual(body["items"][0]["hotelUid"], HOTEL_UID)
        self.assertEqual(body["items"][0]["price"], 10000)

    @patch("reservation.get_conn")
    def test_list_hotels_page_1_size_10(self, mock_get_conn):
        """Ключевой кейс из Postman: page=1 должен вернуть первую страницу."""
        conn, cur = make_db()
        cur.fetchone.return_value = (1,)
        cur.fetchall.return_value = [HOTEL_ROW]
        mock_get_conn.return_value = conn

        r = self.client.get("/api/v1/hotels?page=1&size=10")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["page"], 1)
        self.assertEqual(len(r.get_json()["items"]), 1)

    def test_list_hotels_invalid_params(self):
        r = self.client.get("/api/v1/hotels?page=abc&size=xyz")
        self.assertEqual(r.status_code, 400)

    @patch("reservation.get_conn")
    def test_list_hotels_size_over_100_clamped(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (1,)
        cur.fetchall.return_value = []
        mock_get_conn.return_value = conn
        r = self.client.get("/api/v1/hotels?page=1&size=999")
        self.assertEqual(r.status_code, 200)
        # параметры переданы в SQL: проверим через args второго execute
        calls = [c for c in cur.execute.call_args_list]
        # последний execute — SELECT ... LIMIT %s OFFSET %s
        self.assertEqual(calls[-1][0][1], (100, 0))

    # ---------- GET /api/v1/hotels/<uid> ----------

    @patch("reservation.get_conn")
    def test_get_hotel_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = HOTEL_ROW
        mock_get_conn.return_value = conn

        r = self.client.get(f"/api/v1/hotels/{HOTEL_UID}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["hotelUid"], HOTEL_UID)

    @patch("reservation.get_conn")
    def test_get_hotel_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.get(f"/api/v1/hotels/{HOTEL_UID}")
        self.assertEqual(r.status_code, 404)

    # ---------- GET /api/v1/reservations ----------

    def test_list_reservations_no_header(self):
        r = self.client.get("/api/v1/reservations")
        self.assertEqual(r.status_code, 400)

    @patch("reservation.get_conn")
    def test_list_reservations_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchall.return_value = [RESERVATION_ROW[:11]]
        mock_get_conn.return_value = conn

        r = self.client.get("/api/v1/reservations", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["reservationUid"], RESERVATION_UID)
        self.assertEqual(body[0]["hotel"]["hotelUid"], HOTEL_UID)
        self.assertEqual(body[0]["paymentUid"], PAYMENT_UID)
        self.assertEqual(body[0]["status"], "PAID")
        self.assertEqual(body[0]["startDate"], "2021-10-08")
        self.assertEqual(body[0]["endDate"], "2021-10-11")

    # ---------- GET /api/v1/reservations/<uid> ----------

    def test_get_reservation_no_header(self):
        r = self.client.get(f"/api/v1/reservations/{RESERVATION_UID}")
        self.assertEqual(r.status_code, 400)

    @patch("reservation.get_conn")
    def test_get_reservation_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.get(f"/api/v1/reservations/{RESERVATION_UID}",
                            headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 404)

    @patch("reservation.get_conn")
    def test_get_reservation_foreign_user_hidden(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = RESERVATION_ROW  # username="Test Max"
        mock_get_conn.return_value = conn

        r = self.client.get(f"/api/v1/reservations/{RESERVATION_UID}",
                            headers={"X-User-Name": "Other User"})
        self.assertEqual(r.status_code, 404)

    @patch("reservation.get_conn")
    def test_get_reservation_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = RESERVATION_ROW
        mock_get_conn.return_value = conn

        r = self.client.get(f"/api/v1/reservations/{RESERVATION_UID}",
                            headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["reservationUid"], RESERVATION_UID)

    # ---------- POST /api/v1/reservations (принимает paymentUid) ----------

    def test_create_reservation_no_header(self):
        r = self.client.post("/api/v1/reservations", json={
            "hotelUid": HOTEL_UID, "paymentUid": PAYMENT_UID,
            "startDate": "2021-10-08", "endDate": "2021-10-11",
        })
        self.assertEqual(r.status_code, 400)

    def test_create_reservation_missing_payment_uid(self):
        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Test Max"},
            json={"hotelUid": HOTEL_UID, "startDate": "2021-10-08", "endDate": "2021-10-11"},
        )
        self.assertEqual(r.status_code, 400)

    def test_create_reservation_invalid_dates(self):
        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Test Max"},
            json={"hotelUid": HOTEL_UID, "paymentUid": PAYMENT_UID,
                  "startDate": "2021-10-11", "endDate": "2021-10-08"},
        )
        self.assertEqual(r.status_code, 400)

    @patch("reservation.get_conn")
    def test_create_reservation_hotel_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Test Max"},
            json={"hotelUid": HOTEL_UID, "paymentUid": PAYMENT_UID,
                  "startDate": "2021-10-08", "endDate": "2021-10-11"},
        )
        self.assertEqual(r.status_code, 404)

    @patch("reservation.get_conn")
    def test_create_reservation_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (1,)  # hotels.id
        mock_get_conn.return_value = conn

        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Test Max"},
            json={"hotelUid": HOTEL_UID, "paymentUid": PAYMENT_UID,
                  "startDate": "2021-10-08", "endDate": "2021-10-11"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["hotelUid"], HOTEL_UID)
        self.assertEqual(body["status"], "PAID")
        self.assertEqual(body["startDate"], "2021-10-08")
        self.assertEqual(body["endDate"], "2021-10-11")
        # reservationUid должен быть валидным uuid4
        _uuid.UUID(body["reservationUid"])

    # ---------- DELETE /api/v1/reservations/<uid> ----------

    def test_delete_reservation_no_header(self):
        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}")
        self.assertEqual(r.status_code, 400)

    @patch("reservation.get_conn")
    def test_delete_reservation_not_found(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = None
        mock_get_conn.return_value = conn

        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}",
                               headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 404)

    @patch("reservation.get_conn")
    def test_delete_reservation_foreign_user(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (PAYMENT_UID, "PAID", "Other User")
        mock_get_conn.return_value = conn

        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}",
                               headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 404)

    @patch("reservation.get_conn")
    def test_delete_reservation_already_canceled(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (PAYMENT_UID, "CANCELED", "Test Max")
        mock_get_conn.return_value = conn

        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}",
                               headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 409)

    @patch("reservation.get_conn")
    def test_delete_reservation_ok(self, mock_get_conn):
        conn, cur = make_db()
        cur.fetchone.return_value = (PAYMENT_UID, "PAID", "Test Max")
        mock_get_conn.return_value = conn

        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}",
                               headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["status"], "CANCELED")
        self.assertEqual(body["paymentUid"], PAYMENT_UID)

    def test_health(self):
        r = self.client.get("/manage/health")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()

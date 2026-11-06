import unittest
from unittest.mock import patch, MagicMock
from gateway import app


HOTEL_UID = "049161bb-badd-4fa8-9d90-87c9a82b0668"
PAYMENT_UID = "22222222-2222-2222-2222-222222222222"
RESERVATION_UID = "11111111-1111-1111-1111-111111111111"


def resp(status_code=200, json_body=None):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_body if json_body is not None else {}
    return r


HOTEL_PUBLIC = {
    "hotelUid": HOTEL_UID,
    "name": "Ararat Park Hyatt Moscow",
    "country": "Россия",
    "city": "Москва",
    "address": "Неглинная ул., 4",
    "stars": 5,
    "price": 10000,
}

LOYALTY_PUBLIC = {"status": "GOLD", "discount": 10, "reservationCount": 25}

RESERVATION_INTERNAL = {
    "reservationUid": RESERVATION_UID,
    "hotel": {
        "hotelUid": HOTEL_UID,
        "name": "Ararat Park Hyatt Moscow",
        "fullAddress": "Россия, Москва, Неглинная ул., 4",
        "stars": 5,
    },
    "startDate": "2021-10-08",
    "endDate": "2021-10-11",
    "status": "PAID",
    "paymentUid": PAYMENT_UID,
}


class GatewayTest(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()

    # ---------- GET /api/v1/hotels ----------

    @patch("gateway.requests.get")
    def test_hotels_passthrough(self, mock_get):
        mock_get.return_value = resp(200, {
            "page": 1, "pageSize": 1, "totalElements": 1,
            "items": [HOTEL_PUBLIC],
        })

        r = self.client.get("/api/v1/hotels?page=1&size=10")
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["totalElements"], 1)
        self.assertEqual(body["items"][0]["hotelUid"], HOTEL_UID)

        # проверим, что gateway пошёл в reservation и пробросил params
        args, kwargs = mock_get.call_args
        self.assertTrue(args[0].endswith("/api/v1/hotels"))
        self.assertEqual(kwargs["params"], {"page": "1", "size": "10"})

    @patch("gateway.requests.get")
    def test_hotels_upstream_unavailable(self, mock_get):
        import requests
        mock_get.side_effect = requests.RequestException("boom")
        r = self.client.get("/api/v1/hotels")
        self.assertEqual(r.status_code, 502)

    # ---------- GET /api/v1/loyalty ----------

    def test_loyalty_no_header(self):
        r = self.client.get("/api/v1/loyalty")
        self.assertEqual(r.status_code, 400)

    @patch("gateway.requests.get")
    def test_loyalty_ok(self, mock_get):
        mock_get.return_value = resp(200, LOYALTY_PUBLIC)
        r = self.client.get("/api/v1/loyalty", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "GOLD")

        args, kwargs = mock_get.call_args
        self.assertTrue(args[0].endswith("/api/v1/loyalty"))
        self.assertEqual(kwargs["headers"], {"X-User-Name": "Test Max"})

    @patch("gateway.requests.get")
    def test_loyalty_user_not_found(self, mock_get):
        mock_get.return_value = resp(404, {"message": "User not found"})
        r = self.client.get("/api/v1/loyalty", headers={"X-User-Name": "Nobody"})
        self.assertEqual(r.status_code, 404)

    # ---------- GET /api/v1/reservations ----------

    def test_list_reservations_no_header(self):
        r = self.client.get("/api/v1/reservations")
        self.assertEqual(r.status_code, 400)

    @patch("gateway.requests.get")
    def test_list_reservations_orchestrated_with_payment(self, mock_get):
        """Gateway должен сходить в Reservation, затем в Payment по каждому paymentUid."""
        mock_get.side_effect = [
            resp(200, [RESERVATION_INTERNAL]),
            resp(200, {"paymentUid": PAYMENT_UID, "status": "PAID", "price": 27000}),
        ]

        r = self.client.get("/api/v1/reservations", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["payment"]["status"], "PAID")
        self.assertEqual(body[0]["payment"]["price"], 27000)
        self.assertEqual(body[0]["hotel"]["hotelUid"], HOTEL_UID)
        # paymentUid не должен утечь наружу
        self.assertNotIn("paymentUid", body[0])

        # вызовы: [Reservation, Payment]
        self.assertEqual(mock_get.call_count, 2)
        self.assertTrue(mock_get.call_args_list[0][0][0].endswith("/api/v1/reservations"))
        self.assertTrue(mock_get.call_args_list[1][0][0].endswith(f"/api/v1/payments/{PAYMENT_UID}"))

    # ---------- GET /api/v1/reservations/<uid> ----------

    @patch("gateway.requests.get")
    def test_get_reservation_orchestrated(self, mock_get):
        mock_get.side_effect = [
            resp(200, RESERVATION_INTERNAL),
            resp(200, {"paymentUid": PAYMENT_UID, "status": "PAID", "price": 27000}),
        ]
        r = self.client.get(f"/api/v1/reservations/{RESERVATION_UID}",
                            headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["reservationUid"], RESERVATION_UID)
        self.assertEqual(body["payment"]["price"], 27000)

    @patch("gateway.requests.get")
    def test_get_reservation_not_found(self, mock_get):
        mock_get.return_value = resp(404, {"message": "Reservation not found"})
        r = self.client.get(f"/api/v1/reservations/{RESERVATION_UID}",
                            headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 404)

    # ---------- GET /api/v1/me ----------

    def test_me_no_header(self):
        r = self.client.get("/api/v1/me")
        self.assertEqual(r.status_code, 400)

    @patch("gateway.requests.get")
    def test_me_ok(self, mock_get):
        mock_get.side_effect = [
            resp(200, [RESERVATION_INTERNAL]),                       # Reservation
            resp(200, LOYALTY_PUBLIC),                                # Loyalty
            resp(200, {"paymentUid": PAYMENT_UID, "status": "PAID", "price": 27000}),  # Payment
        ]
        r = self.client.get("/api/v1/me", headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(len(body["reservations"]), 1)
        self.assertEqual(body["reservations"][0]["payment"]["price"], 27000)
        self.assertEqual(body["loyalty"]["status"], "GOLD")
        self.assertEqual(body["loyalty"]["discount"], 10)

    @patch("gateway.requests.get")
    def test_me_loyalty_404(self, mock_get):
        mock_get.side_effect = [
            resp(200, []),
            resp(404, {"message": "User not found"}),
        ]
        r = self.client.get("/api/v1/me", headers={"X-User-Name": "Nobody"})
        self.assertEqual(r.status_code, 404)

    # ---------- POST /api/v1/reservations (главный оркестратор) ----------

    def test_create_reservation_no_header(self):
        r = self.client.post("/api/v1/reservations", json={
            "hotelUid": HOTEL_UID,
            "startDate": "2021-10-08", "endDate": "2021-10-11",
        })
        self.assertEqual(r.status_code, 400)

    def test_create_reservation_invalid_dates(self):
        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Test Max"},
            json={"hotelUid": HOTEL_UID,
                  "startDate": "2021-10-11", "endDate": "2021-10-08"},
        )
        self.assertEqual(r.status_code, 400)

    @patch("gateway.requests.post")
    @patch("gateway.requests.get")
    def test_create_reservation_full_orchestration(self, mock_get, mock_post):
        """
        Порядок:
          1) GET hotels/<uid>            -> Reservation
          2) GET loyalty                 -> Loyalty
          3) POST payments               -> Payment
          4) POST reservations           -> Reservation
          5) POST loyalty/increment      -> Loyalty
        """
        mock_get.side_effect = [
            resp(200, HOTEL_PUBLIC),
            resp(200, LOYALTY_PUBLIC),
        ]
        mock_post.side_effect = [
            resp(200, {"paymentUid": PAYMENT_UID, "status": "PAID", "price": 27000}),
            resp(200, {
                "reservationUid": RESERVATION_UID,
                "hotelUid": HOTEL_UID,
                "startDate": "2021-10-08",
                "endDate": "2021-10-11",
                "status": "PAID",
            }),
            resp(200, {"status": "GOLD", "discount": 10, "reservationCount": 26}),
        ]

        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Test Max"},
            json={"hotelUid": HOTEL_UID,
                  "startDate": "2021-10-08", "endDate": "2021-10-11"},
        )
        self.assertEqual(r.status_code, 200)
        body = r.get_json()
        self.assertEqual(body["reservationUid"], RESERVATION_UID)
        self.assertEqual(body["hotelUid"], HOTEL_UID)
        self.assertEqual(body["discount"], 10)
        self.assertEqual(body["status"], "PAID")
        # 3 ночи * 10000 = 30000, минус 10% = 27000
        self.assertEqual(body["payment"]["price"], 27000)
        self.assertEqual(body["payment"]["status"], "PAID")

        # Проверяем, что Gateway действительно ходил в 3 разных сервиса
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(mock_post.call_count, 3)

        get_urls = [c[0][0] for c in mock_get.call_args_list]
        self.assertTrue(any(u.endswith(f"/api/v1/hotels/{HOTEL_UID}") for u in get_urls))
        self.assertTrue(any(u.endswith("/api/v1/loyalty") for u in get_urls))

        post_urls = [c[0][0] for c in mock_post.call_args_list]
        self.assertTrue(any(u.endswith("/api/v1/payments") for u in post_urls))
        self.assertTrue(any(u.endswith("/api/v1/reservations") for u in post_urls))
        self.assertTrue(any(u.endswith("/api/v1/loyalty/increment") for u in post_urls))

    @patch("gateway.requests.get")
    def test_create_reservation_hotel_not_found(self, mock_get):
        mock_get.return_value = resp(404, {"message": "Hotel not found"})
        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Test Max"},
            json={"hotelUid": HOTEL_UID,
                  "startDate": "2021-10-08", "endDate": "2021-10-11"},
        )
        self.assertEqual(r.status_code, 400)

    @patch("gateway.requests.get")
    def test_create_reservation_loyalty_user_not_found(self, mock_get):
        mock_get.side_effect = [
            resp(200, HOTEL_PUBLIC),
            resp(404, {"message": "User not found"}),
        ]
        r = self.client.post(
            "/api/v1/reservations",
            headers={"X-User-Name": "Nobody"},
            json={"hotelUid": HOTEL_UID,
                  "startDate": "2021-10-08", "endDate": "2021-10-11"},
        )
        self.assertEqual(r.status_code, 404)

    # ---------- DELETE /api/v1/reservations/<uid> ----------

    def test_delete_no_header(self):
        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}")
        self.assertEqual(r.status_code, 400)

    @patch("gateway.requests.post")
    @patch("gateway.requests.delete")
    def test_delete_full_orchestration(self, mock_delete, mock_post):
        """
        Порядок:
          1) DELETE reservations/<uid>     -> Reservation   (возвращает paymentUid)
          2) POST payments/<uid>/cancel    -> Payment
          3) POST loyalty/decrement        -> Loyalty
        """
        mock_delete.return_value = resp(200, {
            "reservationUid": RESERVATION_UID,
            "paymentUid": PAYMENT_UID,
            "status": "CANCELED",
        })
        mock_post.side_effect = [
            resp(200, {"paymentUid": PAYMENT_UID, "status": "CANCELED"}),
            resp(200, {"status": "SILVER", "discount": 7, "reservationCount": 24}),
        ]

        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}",
                               headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 204)

        self.assertEqual(mock_delete.call_count, 1)
        self.assertTrue(mock_delete.call_args[0][0].endswith(f"/api/v1/reservations/{RESERVATION_UID}"))

        self.assertEqual(mock_post.call_count, 2)
        post_urls = [c[0][0] for c in mock_post.call_args_list]
        self.assertTrue(any(u.endswith(f"/api/v1/payments/{PAYMENT_UID}/cancel") for u in post_urls))
        self.assertTrue(any(u.endswith("/api/v1/loyalty/decrement") for u in post_urls))

    @patch("gateway.requests.delete")
    def test_delete_not_found(self, mock_delete):
        mock_delete.return_value = resp(404, {"message": "Reservation not found"})
        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}",
                               headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 404)

    @patch("gateway.requests.delete")
    def test_delete_already_canceled(self, mock_delete):
        mock_delete.return_value = resp(409, {"message": "Reservation already canceled"})
        r = self.client.delete(f"/api/v1/reservations/{RESERVATION_UID}",
                               headers={"X-User-Name": "Test Max"})
        self.assertEqual(r.status_code, 409)

    # ---------- /manage/health ----------

    def test_health(self):
        r = self.client.get("/manage/health")
        self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()

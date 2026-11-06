import os
import math
from datetime import date
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

RESERVATION_URL = os.environ.get("RESERVATION_URL", "http://localhost:8070")
PAYMENT_URL = os.environ.get("PAYMENT_URL", "http://localhost:8060")
LOYALTY_URL = os.environ.get("LOYALTY_URL", "http://localhost:8050")


def _get(url, headers=None, timeout=30):
    return requests.get(url, headers=headers, timeout=timeout)


def _post(url, headers=None, json_body=None, timeout=30):
    return requests.post(url, headers=headers, json=json_body, timeout=timeout)


def _err(message, status):
    return jsonify({"message": message}), status


def _payment_info(payment_uid):
    """Gateway сам ходит в Payment за деталями платежа."""
    try:
        r = _get(f"{PAYMENT_URL}/api/v1/payments/{payment_uid}")
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def _assemble_reservation(raw):
    """Превращает ответ Reservation в публичный формат: добавляет payment."""
    payment = _payment_info(raw["paymentUid"]) or {"status": "PAID", "price": 0}
    return {
        "reservationUid": raw["reservationUid"],
        "hotel": raw["hotel"],
        "startDate": raw["startDate"],
        "endDate": raw["endDate"],
        "status": raw["status"],
        "payment": {"status": payment.get("status", "PAID"), "price": payment.get("price", 0)},
    }


# ============ EXTERNAL ============

@app.route("/manage/health", methods=["GET"])
def health():
    return "Up", 200


@app.route("/api/v1/hotels", methods=["GET"])
def get_hotels():
    try:
        r = _get(f"{RESERVATION_URL}/api/v1/hotels", headers=None)
        r = requests.get(f"{RESERVATION_URL}/api/v1/hotels",
                         params=request.args.to_dict(), timeout=30)
    except requests.RequestException as e:
        return _err(f"Reservation service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify(r.json()), 200


@app.route("/api/v1/loyalty", methods=["GET"])
def get_loyalty():
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    try:
        r = _get(f"{LOYALTY_URL}/api/v1/loyalty", headers={"X-User-Name": username})
    except requests.RequestException as e:
        return _err(f"Loyalty service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify(r.json()), 200


@app.route("/api/v1/reservations", methods=["GET"])
def list_reservations():
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    try:
        r = _get(f"{RESERVATION_URL}/api/v1/reservations",
                 headers={"X-User-Name": username})
    except requests.RequestException as e:
        return _err(f"Reservation service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify([_assemble_reservation(x) for x in r.json()]), 200


@app.route("/api/v1/reservations/<uuid:reservation_uid>", methods=["GET"])
def get_reservation(reservation_uid):
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    try:
        r = _get(f"{RESERVATION_URL}/api/v1/reservations/{reservation_uid}",
                 headers={"X-User-Name": username})
    except requests.RequestException as e:
        return _err(f"Reservation service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify(_assemble_reservation(r.json())), 200


@app.route("/api/v1/me", methods=["GET"])
def get_me():
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    try:
        r_res = _get(f"{RESERVATION_URL}/api/v1/reservations",
                     headers={"X-User-Name": username})
        r_loy = _get(f"{LOYALTY_URL}/api/v1/loyalty",
                     headers={"X-User-Name": username})
    except requests.RequestException as e:
        return _err(f"Upstream service unavailable: {e}", 502)
    if r_loy.status_code != 200:
        return _err(r_loy.json().get("message", "User not found"), r_loy.status_code)
    if r_res.status_code != 200:
        return _err(r_res.json().get("message", "Error"), r_res.status_code)
    reservations = [_assemble_reservation(x) for x in r_res.json()]
    loyalty = r_loy.json()
    return jsonify({
        "reservations": reservations,
        "loyalty": {
            "status": loyalty["status"],
            "discount": loyalty["discount"],
            "reservationCount": loyalty["reservationCount"],
        },
    }), 200


@app.route("/api/v1/reservations", methods=["POST"])
def create_reservation():
    """
    Оркестрация:
      1. Reservation: получить отель по hotelUid.
      2. Loyalty:     получить скидку пользователя.
      3. Gateway:     посчитать ночи, total, final_price.
      4. Payment:     создать платёж → paymentUid.
      5. Reservation: создать бронь с paymentUid.
      6. Loyalty:     increment reservationCount.
      7. Собрать ответ.
    """
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    data = request.get_json(silent=True) or {}
    hotel_uid = data.get("hotelUid")
    start_date_str = data.get("startDate")
    end_date_str = data.get("endDate")
    if not all([hotel_uid, start_date_str, end_date_str]):
        return _err("hotelUid, startDate, endDate are required", 400)
    try:
        sd = date.fromisoformat(start_date_str)
        ed = date.fromisoformat(end_date_str)
    except (ValueError, TypeError):
        return _err("Invalid date format", 400)
    if sd >= ed:
        return _err("startDate must be before endDate", 400)

    # 1. Hotel
    try:
        r = _get(f"{RESERVATION_URL}/api/v1/hotels/{hotel_uid}")
    except requests.RequestException as e:
        return _err(f"Reservation service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err("Hotel not found", 400)
    hotel = r.json()

    # 2. Loyalty
    try:
        r = _get(f"{LOYALTY_URL}/api/v1/loyalty",
                 headers={"X-User-Name": username})
    except requests.RequestException as e:
        return _err(f"Loyalty service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "User not found"), r.status_code)
    loyalty = r.json()
    discount = int(loyalty["discount"])

    # 3. Compute
    nights = (ed - sd).days
    total = hotel["price"] * nights
    final_price = math.ceil(total * (100 - discount) / 100)

    # 4. Payment
    try:
        r = _post(f"{PAYMENT_URL}/api/v1/payments", json_body={"price": final_price})
    except requests.RequestException as e:
        return _err(f"Payment service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "Payment error"), r.status_code)
    payment = r.json()
    payment_uid = payment["paymentUid"]

    # 5. Reservation
    try:
        r = _post(
            f"{RESERVATION_URL}/api/v1/reservations",
            headers={"X-User-Name": username},
            json_body={
                "hotelUid": hotel_uid,
                "paymentUid": payment_uid,
                "startDate": start_date_str,
                "endDate": end_date_str,
            },
        )
    except requests.RequestException as e:
        return _err(f"Reservation service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "Reservation error"), r.status_code)
    reservation = r.json()

    # 6. Increment loyalty (best-effort: бронь уже создана)
    try:
        _post(f"{LOYALTY_URL}/api/v1/loyalty/increment",
              headers={"X-User-Name": username})
    except requests.RequestException:
        pass

    # 7. Response
    return jsonify({
        "reservationUid": reservation["reservationUid"],
        "hotelUid": hotel_uid,
        "startDate": start_date_str,
        "endDate": end_date_str,
        "discount": discount,
        "status": reservation["status"],
        "payment": {"status": payment["status"], "price": payment["price"]},
    }), 200


@app.route("/api/v1/reservations/<uuid:reservation_uid>", methods=["DELETE"])
def delete_reservation(reservation_uid):
    """
    Оркестрация:
      1. Reservation: пометить CANCELED → отдаёт paymentUid.
      2. Payment:     cancel.
      3. Loyalty:     decrement.
    """
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)

    # 1. Cancel reservation
    try:
        r = requests.delete(
            f"{RESERVATION_URL}/api/v1/reservations/{reservation_uid}",
            headers={"X-User-Name": username}, timeout=30,
        )
    except requests.RequestException as e:
        return _err(f"Reservation service unavailable: {e}", 502)
    if r.status_code != 200:
        return _err(r.json().get("message", "Reservation error"), r.status_code)
    payment_uid = r.json()["paymentUid"]

    # 2. Cancel payment
    try:
        _post(f"{PAYMENT_URL}/api/v1/payments/{payment_uid}/cancel")
    except requests.RequestException:
        pass

    # 3. Decrement loyalty
    try:
        _post(f"{LOYALTY_URL}/api/v1/loyalty/decrement",
              headers={"X-User-Name": username})
    except requests.RequestException:
        pass

    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

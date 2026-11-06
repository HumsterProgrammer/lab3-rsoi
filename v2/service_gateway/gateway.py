import os
import math
import threading
import queue
import time
from datetime import date
import requests
from flask import Flask, request, jsonify

app = Flask(__name__)

RESERVATION_URL = os.environ.get("RESERVATION_URL", "http://localhost:8070")
PAYMENT_URL = os.environ.get("PAYMENT_URL", "http://localhost:8060")
LOYALTY_URL = os.environ.get("LOYALTY_URL", "http://localhost:8050")

# Fallback для деградации /me
FALLBACK_LOYALTY = {"status": "BRONZE", "discount": 5, "reservationCount": 0}

# ============ Retry queue (для DELETE loyalty decrement) ============
_retry_queue = queue.Queue()


def _loyalty_retry_worker():
    """Background worker: retries loyalty decrement until success or permanent 4xx."""
    while True:
        username = _retry_queue.get()
        attempt = 0
        while True:
            attempt += 1
            try:
                r = requests.post(
                    f"{LOYALTY_URL}/api/v1/loyalty/decrement",
                    headers={"X-User-Name": username},
                    timeout=10,
                )
                if 200 <= r.status_code < 300:
                    break
                if 400 <= r.status_code < 500:
                    # permanent error (user not found etc.) — stop
                    break
                # 5xx — retry
            except requests.RequestException:
                pass
            # экспоненциальный backoff, cap 30s
            time.sleep(min(2 ** min(attempt, 5), 30))


threading.Thread(target=_loyalty_retry_worker, daemon=True).start()


# ============ Helpers ============
def _err(msg, status):
    return jsonify({"message": msg}), status


def _payment_info(payment_uid):
    """Return payment dict or None if Payment service unavailable."""
    try:
        r = requests.get(f"{PAYMENT_URL}/api/v1/payments/{payment_uid}", timeout=10)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def _assemble_reservation(raw):
    """Public reservation; payment={} если Payment Service недоступен."""
    payment = _payment_info(raw["paymentUid"])
    base = {
        "reservationUid": raw["reservationUid"],
        "hotel": raw["hotel"],
        "startDate": raw["startDate"],
        "endDate": raw["endDate"],
        "status": raw["status"],
    }
    if payment is None:
        base["payment"] = {}
    else:
        base["payment"] = {
            "status": payment.get("status"),
            "price": payment.get("price"),
        }
    return base


def _rollback_payment(payment_uid):
    """Best-effort cancel of a payment (used for rollback)."""
    try:
        requests.post(f"{PAYMENT_URL}/api/v1/payments/{payment_uid}/cancel", timeout=10)
    except requests.RequestException:
        pass


# ============ EXTERNAL ============

@app.route("/manage/health", methods=["GET"])
def health():
    return "Up", 200


@app.route("/api/v1/hotels", methods=["GET"])
def get_hotels():
    """Reservation критичен: 500 если недоступен."""
    try:
        r = requests.get(
            f"{RESERVATION_URL}/api/v1/hotels",
            params=request.args.to_dict(),
            timeout=10,
        )
    except requests.RequestException:
        return _err("Reservation service unavailable", 500)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify(r.json()), 200


@app.route("/api/v1/loyalty", methods=["GET"])
def get_loyalty():
    """Loyalty критичен: 500 если недоступен."""
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    try:
        r = requests.get(
            f"{LOYALTY_URL}/api/v1/loyalty",
            headers={"X-User-Name": username},
            timeout=10,
        )
    except requests.RequestException:
        return _err("Loyalty service unavailable", 500)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify(r.json()), 200


@app.route("/api/v1/reservations", methods=["GET"])
def list_reservations():
    """Reservation критичен; Payment — fallback по каждой брони."""
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    try:
        r = requests.get(
            f"{RESERVATION_URL}/api/v1/reservations",
            headers={"X-User-Name": username},
            timeout=10,
        )
    except requests.RequestException:
        return _err("Reservation service unavailable", 500)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify([_assemble_reservation(x) for x in r.json()]), 200


@app.route("/api/v1/reservations/<uuid:reservation_uid>", methods=["GET"])
def get_reservation(reservation_uid):
    """Reservation критичен; Payment — fallback."""
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)
    try:
        r = requests.get(
            f"{RESERVATION_URL}/api/v1/reservations/{reservation_uid}",
            headers={"X-User-Name": username},
            timeout=10,
        )
    except requests.RequestException:
        return _err("Reservation service unavailable", 500)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    return jsonify(_assemble_reservation(r.json())), 200


@app.route("/api/v1/me", methods=["GET"])
def get_me():
    """Reservation критичен; Payment и Loyalty — fallback."""
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)

    # Reservation — critical
    try:
        r_res = requests.get(
            f"{RESERVATION_URL}/api/v1/reservations",
            headers={"X-User-Name": username},
            timeout=10,
        )
    except requests.RequestException:
        return _err("Reservation service unavailable", 500)
    if r_res.status_code != 200:
        return _err(r_res.json().get("message", "Error"), r_res.status_code)

    # Payment — fallback per-reservation (внутри _assemble_reservation)
    reservations = [_assemble_reservation(x) for x in r_res.json()]

    # Loyalty — fallback on unavailability, 404 propagates
    try:
        r_loy = requests.get(
            f"{LOYALTY_URL}/api/v1/loyalty",
            headers={"X-User-Name": username},
            timeout=10,
        )
        if r_loy.status_code == 200:
            data = r_loy.json()
            loyalty = {
                "status": data["status"],
                "discount": data["discount"],
                "reservationCount": data["reservationCount"],
            }
        elif r_loy.status_code == 404:
            return _err("User not found", 404)
        else:
            loyalty = dict(FALLBACK_LOYALTY)
    except requests.RequestException:
        loyalty = dict(FALLBACK_LOYALTY)

    return jsonify({"reservations": reservations, "loyalty": loyalty}), 200


@app.route("/api/v1/reservations", methods=["POST"])
def create_reservation():
    """
    Оркестрация с деградацией:
      1) Reservation hotels/<uid>  — критично, 500 если недоступен
      2) Loyalty    GET loyalty    — критично (нужен discount для цены)
      3) compute final_price
      4) Payment    POST payments  — критично, 500 если недоступен
      5) Reservation POST reservation
      6) Loyalty    POST increment — если упал: rollback payment + reservation, 500
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

    # 1. Hotel check
    try:
        r = requests.get(f"{RESERVATION_URL}/api/v1/hotels/{hotel_uid}", timeout=10)
    except requests.RequestException:
        return _err("Reservation service unavailable", 500)
    if r.status_code == 404:
        return _err("Hotel not found", 400)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    hotel = r.json()

    # 2. Discount (без него нельзя посчитать цену)
    try:
        r = requests.get(
            f"{LOYALTY_URL}/api/v1/loyalty",
            headers={"X-User-Name": username},
            timeout=10,
        )
    except requests.RequestException:
        return _err("Loyalty service unavailable", 500)
    if r.status_code != 200:
        return _err(r.json().get("message", "User not found"), r.status_code)
    loyalty = r.json()
    discount = int(loyalty["discount"])

    # 3. Compute price
    nights = (ed - sd).days
    total = hotel["price"] * nights
    final_price = math.ceil(total * (100 - discount) / 100)

    # 4. Payment — critical
    try:
        r = requests.post(
            f"{PAYMENT_URL}/api/v1/payments",
            json={"price": final_price},
            timeout=10,
        )
    except requests.RequestException:
        return _err("Payment service unavailable", 500)
    if r.status_code != 200:
        return _err(r.json().get("message", "Payment error"), r.status_code)
    payment = r.json()
    payment_uid = payment["paymentUid"]

    # 5. Create reservation
    try:
        r = requests.post(
            f"{RESERVATION_URL}/api/v1/reservations",
            headers={"X-User-Name": username},
            json={
                "hotelUid": hotel_uid,
                "paymentUid": payment_uid,
                "startDate": start_date_str,
                "endDate": end_date_str,
            },
            timeout=10,
        )
    except requests.RequestException:
        _rollback_payment(payment_uid)
        return _err("Reservation service unavailable", 500)
    if r.status_code != 200:
        _rollback_payment(payment_uid)
        return _err(r.json().get("message", "Reservation error"), r.status_code)
    reservation = r.json()
    reservation_uid = reservation["reservationUid"]

    # 6. Increment loyalty — если упал, откат payment и reservation
    loyalty_failed = False
    try:
        r = requests.post(
            f"{LOYALTY_URL}/api/v1/loyalty/increment",
            headers={"X-User-Name": username},
            timeout=10,
        )
        if not (200 <= r.status_code < 300):
            loyalty_failed = True
    except requests.RequestException:
        loyalty_failed = True

    if loyalty_failed:
        # rollback payment
        _rollback_payment(payment_uid)
        # rollback reservation (best-effort)
        try:
            requests.delete(
                f"{RESERVATION_URL}/api/v1/reservations/{reservation_uid}",
                headers={"X-User-Name": username},
                timeout=10,
            )
        except requests.RequestException:
            pass
        return _err("Loyalty service unavailable", 500)

    return jsonify({
        "reservationUid": reservation_uid,
        "hotelUid": hotel_uid,
        "startDate": start_date_str,
        "endDate": end_date_str,
        "discount": discount,
        "status": "PAID",
        "payment": {
            "status": payment.get("status", "PAID"),
            "price": payment.get("price", final_price),
        },
    }), 200


@app.route("/api/v1/reservations/<uuid:reservation_uid>", methods=["DELETE"])
def delete_reservation(reservation_uid):
    """
    Оркестрация отмены:
      1) Reservation GET  — проверка владельца + paymentUid (критично)
      2) Payment   cancel — критично, 500 если недоступен (ничего ещё не меняли)
      3) Reservation DELETE — помечаем CANCELED
      4) Loyalty   decrement — если упал, ставим в retry-очередь, отдаём 204
    """
    username = request.headers.get("X-User-Name")
    if not username:
        return _err("X-User-Name header is required", 400)

    # 1. Fetch reservation (verify + payment_uid)
    try:
        r = requests.get(
            f"{RESERVATION_URL}/api/v1/reservations/{reservation_uid}",
            headers={"X-User-Name": username},
            timeout=10,
        )
    except requests.RequestException:
        return _err("Reservation service unavailable", 500)
    if r.status_code == 404:
        return _err("Reservation not found", 404)
    if r.status_code != 200:
        return _err(r.json().get("message", "Error"), r.status_code)
    reservation = r.json()
    if reservation.get("status") == "CANCELED":
        return _err("Reservation already canceled", 409)
    payment_uid = reservation["paymentUid"]

    # 2. Cancel payment — critical
    try:
        r = requests.post(
            f"{PAYMENT_URL}/api/v1/payments/{payment_uid}/cancel",
            timeout=10,
        )
    except requests.RequestException:
        return _err("Payment service unavailable", 500)
    if r.status_code != 200:
        return _err(r.json().get("message", "Payment cancel failed"), r.status_code)

    # 3. Mark reservation CANCELED
    try:
        requests.delete(
            f"{RESERVATION_URL}/api/v1/reservations/{reservation_uid}",
            headers={"X-User-Name": username},
            timeout=10,
        )
    except requests.RequestException:
        # крайний случай: payment уже отменён, reservation не помечен —
        # но пользователю важнее, что бронь больше не активна
        pass

    # 4. Decrement loyalty — queued on failure
    try:
        r = requests.post(
            f"{LOYALTY_URL}/api/v1/loyalty/decrement",
            headers={"X-User-Name": username},
            timeout=10,
        )
        if not (200 <= r.status_code < 300):
            _retry_queue.put(username)
    except requests.RequestException:
        _retry_queue.put(username)

    return "", 204


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

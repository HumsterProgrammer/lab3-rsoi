import os
import uuid
from datetime import date
import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", 5432)),
    "dbname": os.environ.get("DB_NAME", "reservations"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", "postgres"),
}


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def _hotel_public(row):
    return {
        "hotelUid": str(row[0]),
        "name": row[1],
        "country": row[2],
        "city": row[3],
        "address": row[4],
        "stars": row[5],
        "price": row[6],
    }


# -------- HOTELS --------

@app.route("/manage/health", methods=["GET"])
def health():
    return "Up", 200


@app.route("/api/v1/hotels", methods=["GET"])
def list_hotels():
    try:
        page = int(request.args.get("page", 1))
        size = int(request.args.get("size", 10))
    except (TypeError, ValueError):
        return jsonify({"message": "Invalid pagination params"}), 400
    if page < 1:
        page = 1
    if size < 1:
        size = 10
    if size > 100:
        size = 100
    offset = (page - 1) * size
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM hotels")
        total = cur.fetchone()[0]
        cur.execute(
            "SELECT hotel_uid, name, country, city, address, stars, price "
            "FROM hotels ORDER BY id LIMIT %s OFFSET %s",
            (size, offset),
        )
        rows = cur.fetchall()
    conn.close()
    return jsonify({
        "page": page,
        "pageSize": len(rows),
        "totalElements": total,
        "items": [_hotel_public(r) for r in rows],
    }), 200


@app.route("/api/v1/hotels/<hotel_uid>", methods=["GET"])
def get_hotel(hotel_uid):
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT hotel_uid, name, country, city, address, stars, price "
            "FROM hotels WHERE hotel_uid=%s",
            (hotel_uid,),
        )
        row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"message": "Hotel not found"}), 404
    return jsonify(_hotel_public(row)), 200


# -------- RESERVATIONS --------

def _reservation_row(row):
    """row: (reservation_uid, status, start_date, end_date, payment_uid,
             hotel_uid, name, country, city, address, stars)"""
    return {
        "reservationUid": str(row[0]),
        "hotel": {
            "hotelUid": str(row[5]),
            "name": row[6],
            "fullAddress": f"{row[7]}, {row[8]}, {row[9]}",
            "stars": row[10],
        },
        "startDate": row[2].date().isoformat() if row[2] else None,
        "endDate": row[3].date().isoformat() if row[3] else None,
        "status": row[1],
        "paymentUid": str(row[4]),
    }


@app.route("/api/v1/reservations", methods=["GET"])
def list_reservations():
    username = request.headers.get("X-User-Name")
    if not username:
        return jsonify({"message": "X-User-Name header is required"}), 400
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.reservation_uid, r.status, r.start_date, r.end_date, r.payment_uid,
                   h.hotel_uid, h.name, h.country, h.city, h.address, h.stars
            FROM reservation r
            JOIN hotels h ON h.id = r.hotel_id
            WHERE r.username = %s
            ORDER BY r.id
        """, (username,))
        rows = cur.fetchall()
    conn.close()
    return jsonify([_reservation_row(r) for r in rows]), 200


@app.route("/api/v1/reservations/<reservation_uid>", methods=["GET"])
def get_reservation(reservation_uid):
    username = request.headers.get("X-User-Name")
    if not username:
        return jsonify({"message": "X-User-Name header is required"}), 400
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute("""
            SELECT r.reservation_uid, r.status, r.start_date, r.end_date, r.payment_uid,
                   h.hotel_uid, h.name, h.country, h.city, h.address, h.stars,
                   r.username
            FROM reservation r
            JOIN hotels h ON h.id = r.hotel_id
            WHERE r.reservation_uid = %s
        """, (reservation_uid,))
        row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"message": "Reservation not found"}), 404
    if row[11] != username:
        return jsonify({"message": "Reservation does not belong to user"}), 404
    return jsonify(_reservation_row(row[:11])), 200


@app.route("/api/v1/reservations", methods=["POST"])
def create_reservation():
    """Принимает всё уже подготовленное Gateway: paymentUid, hotelUid, даты."""
    username = request.headers.get("X-User-Name")
    if not username:
        return jsonify({"message": "X-User-Name header is required"}), 400
    data = request.get_json(silent=True) or {}
    hotel_uid = data.get("hotelUid")
    payment_uid = data.get("paymentUid")
    start_date_str = data.get("startDate")
    end_date_str = data.get("endDate")
    if not all([hotel_uid, payment_uid, start_date_str, end_date_str]):
        return jsonify({"message": "hotelUid, paymentUid, startDate, endDate are required"}), 400
    try:
        sd = date.fromisoformat(start_date_str)
        ed = date.fromisoformat(end_date_str)
    except ValueError:
        return jsonify({"message": "Invalid date format"}), 400
    if sd >= ed:
        return jsonify({"message": "startDate must be before endDate"}), 400

    conn = get_conn()
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM hotels WHERE hotel_uid=%s", (hotel_uid,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"message": "Hotel not found"}), 404
        hotel_id = row[0]
        reservation_uid = str(uuid.uuid4())
        cur.execute(
            "INSERT INTO reservation "
            "(reservation_uid, username, payment_uid, hotel_id, status, start_date, end_date) "
            "VALUES (%s, %s, %s, %s, 'PAID', %s, %s)",
            (reservation_uid, username, payment_uid, hotel_id, sd, ed),
        )
    conn.close()
    return jsonify({
        "reservationUid": reservation_uid,
        "hotelUid": hotel_uid,
        "startDate": start_date_str,
        "endDate": end_date_str,
        "status": "PAID",
    }), 200


@app.route("/api/v1/reservations/<reservation_uid>", methods=["DELETE"])
def delete_reservation(reservation_uid):
    """Просто помечает CANCELED. Возвращает paymentUid для оркестрации."""
    username = request.headers.get("X-User-Name")
    if not username:
        return jsonify({"message": "X-User-Name header is required"}), 400
    conn = get_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT payment_uid, status, username FROM reservation WHERE reservation_uid=%s FOR UPDATE",
            (reservation_uid,),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return jsonify({"message": "Reservation not found"}), 404
        if row[2] != username:
            conn.close()
            return jsonify({"message": "Reservation does not belong to user"}), 404
        payment_uid, status, _ = row
        if status == "CANCELED":
            conn.close()
            return jsonify({"message": "Reservation already canceled"}), 409
        cur.execute(
            "UPDATE reservation SET status='CANCELED' WHERE reservation_uid=%s",
            (reservation_uid,),
        )
    conn.close()
    return jsonify({"reservationUid": str(reservation_uid),
                    "paymentUid": str(payment_uid),
                    "status": "CANCELED"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8070)

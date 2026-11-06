import os
import psycopg2
from flask import Flask, request, jsonify

app = Flask(__name__)

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": int(os.environ.get("DB_PORT", 5432)),
    "dbname": os.environ.get("DB_NAME", "loyalties"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", "postgres"),
}


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def compute_status(count: int):
    if count >= 20:
        return "GOLD", 10
    if count >= 10:
        return "SILVER", 7
    return "BRONZE", 5


@app.route("/manage/health", methods=["GET"])
def health():
    return "Up", 200


@app.route("/api/v1/loyalty", methods=["GET"])
def get_loyalty():
    username = request.headers.get("X-User-Name")
    if not username:
        return jsonify({"message": "X-User-Name header is required"}), 400
    conn = get_conn()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT reservation_count, status, discount FROM loyalty WHERE username=%s",
            (username,),
        )
        row = cur.fetchone()
    conn.close()
    if not row:
        return jsonify({"message": "User not found"}), 404
    return jsonify({"status": row[1], "discount": row[2], "reservationCount": row[0]}), 200


def _change_count(username, delta):
    conn = get_conn()
    with conn, conn.cursor() as cur:
        cur.execute(
            "SELECT reservation_count FROM loyalty WHERE username=%s FOR UPDATE",
            (username,),
        )
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        new_count = max(0, row[0] + delta)
        status, discount = compute_status(new_count)
        cur.execute(
            "UPDATE loyalty SET reservation_count=%s, status=%s, discount=%s WHERE username=%s",
            (new_count, status, discount, username),
        )
    conn.close()
    return {"status": status, "discount": discount, "reservationCount": new_count}


@app.route("/api/v1/loyalty/increment", methods=["POST"])
def increment():
    username = request.headers.get("X-User-Name")
    if not username:
        return jsonify({"message": "X-User-Name header is required"}), 400
    result = _change_count(username, +1)
    if result is None:
        return jsonify({"message": "User not found"}), 404
    return jsonify(result), 200


@app.route("/api/v1/loyalty/decrement", methods=["POST"])
def decrement():
    username = request.headers.get("X-User-Name")
    if not username:
        return jsonify({"message": "X-User-Name header is required"}), 400
    result = _change_count(username, -1)
    if result is None:
        return jsonify({"message": "User not found"}), 404
    return jsonify(result), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8050)

"""
UltraLauncher Network Server
Создание и получение кодов для игры по сети.
"""

import os
import random
import string
import time
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor


app = Flask(__name__)

# === Конфиг ===
DATABASE_URL = os.environ.get("DATABASE_URL", "")
CODE_TTL_MINUTES = 30  # сколько живёт код


# ==================== БАЗА ДАННЫХ ====================
def get_db():
    """Подключение к Neon PostgreSQL."""
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    """Создаёт таблицу codes если её нет."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS codes (
                        code VARCHAR(16) PRIMARY KEY,
                        ip VARCHAR(64) NOT NULL,
                        port INTEGER DEFAULT 25565,
                        version VARCHAR(32) DEFAULT '',
                        created_at TIMESTAMP DEFAULT NOW(),
                        expires_at TIMESTAMP NOT NULL
                    )
                """)
                conn.commit()
        print("✅ Таблица codes готова")
    except Exception as e:
        print(f"❌ Ошибка init_db: {e}")


def generate_code():
    """Генерирует код типа ABCD-1234."""
    letters = "".join(random.choices(string.ascii_uppercase, k=4))
    digits = "".join(random.choices(string.digits, k=4))
    return f"{letters}-{digits}"


def cleanup_expired():
    """Удаляет просроченные коды."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM codes WHERE expires_at < NOW()")
                deleted = cur.rowcount
                conn.commit()
                if deleted > 0:
                    print(f"🧹 Удалено просроченных: {deleted}")
    except Exception as e:
        print(f"Ошибка cleanup: {e}")


# ==================== API ====================
@app.route("/", methods=["GET"])
def index():
    """Проверка что сервер жив."""
    return jsonify({
        "status": "ok",
        "service": "UltraLauncher Network",
        "time": datetime.now().isoformat(),
    })


@app.route("/create", methods=["POST"])
def create_code():
    """Создаёт код для игры по сети."""
    try:
        data = request.get_json()
        ip = data.get("ip", "").strip()
        port = int(data.get("port", 25565))
        version = data.get("version", "").strip()

        if not ip:
            return jsonify({"error": "IP не указан"}), 400

        # Чистим просроченные
        cleanup_expired()

        # Генерим уникальный код
        for _ in range(10):
            code = generate_code()
            expires = datetime.now() + timedelta(minutes=CODE_TTL_MINUTES)

            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO codes (code, ip, port, version, expires_at)
                            VALUES (%s, %s, %s, %s, %s)
                        """, (code, ip, port, version, expires))
                        conn.commit()

                return jsonify({
                    "code": code,
                    "expires_at": expires.isoformat(),
                    "ttl_minutes": CODE_TTL_MINUTES,
                })

            except psycopg2.IntegrityError:
                # Код уже есть — генерим заново
                continue

        return jsonify({"error": "Не удалось создать код"}), 500

    except Exception as e:
        print(f"Ошибка /create: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/get/<code>", methods=["GET"])
def get_code(code):
    """Получает IP по коду."""
    try:
        code = code.upper().strip()
        cleanup_expired()

        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT ip, port, version, expires_at
                    FROM codes
                    WHERE code = %s AND expires_at > NOW()
                """, (code,))
                row = cur.fetchone()

        if not row:
            return jsonify({"error": "Код не найден или истёк"}), 404

        return jsonify({
            "ip": row["ip"],
            "port": row["port"],
            "version": row["version"],
            "expires_at": row["expires_at"].isoformat(),
        })

    except Exception as e:
        print(f"Ошибка /get: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/stats", methods=["GET"])
def stats():
    """Статистика сервера."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM codes WHERE expires_at > NOW()")
                active = cur.fetchone()[0]
        return jsonify({"active_codes": active})
    except Exception:
        return jsonify({"active_codes": 0})


# ==================== ЗАПУСК ====================
# Инициализируем БД при старте (для Render)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
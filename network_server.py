"""
UltraLauncher Network Server
Создание и получение кодов для игры по сети (с поддержкой EasyTier P2P).
"""

import os
import random
import string
from datetime import datetime, timedelta

from flask import Flask, request, jsonify
import psycopg2
from psycopg2.extras import RealDictCursor


app = Flask(__name__)

# === Конфиг ===
DATABASE_URL = os.environ.get("DATABASE_URL", "")
CODE_TTL_MINUTES = 60  # код живёт час (было 30)


# ==================== БАЗА ДАННЫХ ====================
def get_db():
    """Подключение к Neon PostgreSQL."""
    return psycopg2.connect(DATABASE_URL, sslmode="require")


def init_db():
    """Создаёт таблицу codes если её нет, добавляет новые колонки."""
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                # Основная таблица
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS codes (
                        code VARCHAR(16) PRIMARY KEY,
                        ip VARCHAR(64) NOT NULL,
                        port INTEGER DEFAULT 25565,
                        version VARCHAR(32) DEFAULT '',
                        network_name VARCHAR(64) DEFAULT '',
                        network_secret VARCHAR(64) DEFAULT '',
                        virtual_ip VARCHAR(64) DEFAULT '',
                        created_at TIMESTAMP DEFAULT NOW(),
                        expires_at TIMESTAMP NOT NULL
                    )
                """)

                # Добавляем новые колонки, если таблица была старой версии
                cur.execute("""
                    ALTER TABLE codes
                    ADD COLUMN IF NOT EXISTS network_name VARCHAR(64) DEFAULT ''
                """)
                cur.execute("""
                    ALTER TABLE codes
                    ADD COLUMN IF NOT EXISTS network_secret VARCHAR(64) DEFAULT ''
                """)
                cur.execute("""
                    ALTER TABLE codes
                    ADD COLUMN IF NOT EXISTS virtual_ip VARCHAR(64) DEFAULT ''
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


def generate_network_name():
    """Генерирует уникальное имя сети."""
    return "ultra_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10))


def generate_network_secret():
    """Генерирует секрет сети."""
    return "".join(random.choices(string.ascii_letters + string.digits, k=20))


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
    """
    Создаёт код для игры по сети.
    Поддерживает два режима:
    - Обычный (просто IP): {ip, port, version}
    - EasyTier P2P: {network_name, network_secret, virtual_ip, port, version}
    """
    try:
        data = request.get_json()
        ip = data.get("ip", "").strip()
        port = int(data.get("port", 25565))
        version = data.get("version", "").strip()
        network_name = data.get("network_name", "").strip()
        network_secret = data.get("network_secret", "").strip()
        virtual_ip = data.get("virtual_ip", "").strip()

        # Если есть virtual_ip — используем его, иначе обычный ip
        if virtual_ip:
            ip = virtual_ip

        if not ip and not network_name:
            return jsonify({"error": "Укажи IP или network_name"}), 400

        cleanup_expired()

        # Если network_name не передан — генерим сами
        if not network_name:
            network_name = generate_network_name()
        if not network_secret:
            network_secret = generate_network_secret()

        for _ in range(10):
            code = generate_code()
            expires = datetime.now() + timedelta(minutes=CODE_TTL_MINUTES)

            try:
                with get_db() as conn:
                    with conn.cursor() as cur:
                        cur.execute("""
                            INSERT INTO codes
                                (code, ip, port, version, network_name,
                                 network_secret, virtual_ip, expires_at)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """, (code, ip, port, version, network_name,
                              network_secret, virtual_ip, expires))

                        conn.commit()

                return jsonify({
                    "code": code,
                    "network_name": network_name,
                    "network_secret": network_secret,
                    "virtual_ip": virtual_ip or ip,
                    "port": port,
                    "expires_at": expires.isoformat(),
                    "ttl_minutes": CODE_TTL_MINUTES,
                })

            except psycopg2.IntegrityError:
                continue

        return jsonify({"error": "Не удалось создать код"}), 500

    except Exception as e:
        print(f"Ошибка /create: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/get/<code>", methods=["GET"])
def get_code(code):
    """Получает IP/сетевые данные по коду."""
    try:
        code = code.upper().strip()
        cleanup_expired()

        with get_db() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT ip, port, version,
                           network_name, network_secret, virtual_ip,
                           expires_at
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
            "network_name": row["network_name"] or "",
            "network_secret": row["network_secret"] or "",
            "virtual_ip": row["virtual_ip"] or row["ip"],
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
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

"""Простая проверка Command Bar через запущенный FastAPI-сервер.

Запуск:
    python scripts/check_ai_command.py
    python scripts/check_ai_command.py "Закрой S-24"
"""

from __future__ import annotations

import argparse
import json

import requests

API_URL = "http://127.0.0.1:8000/api/ai/command"
DEFAULT_COMMAND = "Курьер задержится до 15:00 для P-08"


def main() -> None:
    parser = argparse.ArgumentParser(description="Проверка /api/ai/command")
    parser.add_argument("command", nargs="?", default=DEFAULT_COMMAND)
    args = parser.parse_args()

    response = requests.post(
        API_URL,
        json={"command": args.command},
        timeout=30,
    )
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

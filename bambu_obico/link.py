from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

import requests
from dotenv import dotenv_values


def verify_code(server: str, code: str) -> str:
    url = server.rstrip("/") + "/api/v1/octo/verify/"
    response = requests.post(url, params={"code": code.strip()}, timeout=15)
    response.raise_for_status()
    data = response.json()
    token = data.get("printer", {}).get("auth_token")
    if not token:
        raise RuntimeError("Obico verification response did not contain printer.auth_token")
    return token


def save_token(env_path: Path, token: str) -> None:
    values = dotenv_values(env_path) if env_path.exists() else {}
    lines = env_path.read_text().splitlines() if env_path.exists() else []

    replacement = f"OBICO_AUTH_TOKEN={token}"
    found = False
    output = []
    for line in lines:
        if line.startswith("OBICO_AUTH_TOKEN="):
            output.append(replacement)
            found = True
        else:
            output.append(line)
    if not found:
        if output and output[-1] != "":
            output.append("")
        output.append(replacement)

    env_path.write_text("\n".join(output) + "\n")
    os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Link bambu-obico to an Obico printer")
    parser.add_argument("--env", default=".env", help="Local env file to update")
    args = parser.parse_args()

    env_path = Path(args.env)
    values = dotenv_values(env_path)
    server = (values.get("OBICO_SERVER") or os.getenv("OBICO_SERVER") or "").strip()
    if not server:
        raise SystemExit("Set OBICO_SERVER in .env first")

    print(f"Obico server: {server}")
    print("In Obico, add a new Klipper-type printer and obtain its 6-digit verification code.")
    code = input("Enter 6-digit verification code (empty to abort): ").strip()
    if not code:
        raise SystemExit(1)

    try:
        token = verify_code(server, code)
    except requests.RequestException as exc:
        raise SystemExit(f"Linking failed: {exc}") from exc
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(f"Linking failed: {exc}") from exc

    save_token(env_path, token)
    print("Successfully linked. OBICO_AUTH_TOKEN was saved to the local .env with mode 0600.")
    print("The token was not printed.")


if __name__ == "__main__":
    main()

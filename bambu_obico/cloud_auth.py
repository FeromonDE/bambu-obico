from __future__ import annotations

import argparse
import getpass
import json
import os
from pathlib import Path

import requests

API = "https://api.bambulab.com"
DEFAULT_PATH = Path.home() / ".config" / "bambu-obico" / "cloud.json"


def login(email: str, password: str | None = None, code: str | None = None) -> dict:
    payload = {"account": email}
    if code:
        payload["code"] = code
    elif password:
        payload["password"] = password
    else:
        raise ValueError("password or verification code is required")
    r = requests.post(f"{API}/v1/user-service/user/login", json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("accessToken"):
        return data
    token = data["accessToken"]
    p = requests.get(
        f"{API}/v1/design-user-service/my/preference",
        headers={"Authorization": f"Bearer {token}"},
        timeout=20,
    )
    p.raise_for_status()
    uid = str(p.json()["uid"])
    return {"accessToken": token, "userId": uid, "expiresIn": data.get("expiresIn")}


def save_credentials(data: dict, path: Path = DEFAULT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)


def main() -> None:
    ap = argparse.ArgumentParser(description="Store Bambu Cloud credentials locally")
    ap.add_argument("--output", type=Path, default=DEFAULT_PATH)
    args = ap.parse_args()
    email = input("Bambu account email: ").strip()
    password = getpass.getpass("Bambu password (input hidden): ")
    data = login(email, password=password)
    if not data.get("accessToken") and data.get("loginType") == "verifyCode":
        code = input("Verification code: ").strip()
        data = login(email, code=code)
    if not data.get("accessToken"):
        raise SystemExit("Login did not return an access token.")
    save_credentials(data, args.output)
    print(f"Credentials saved to {args.output} (mode 0600)")
    print(f"Cloud MQTT username: u_{data['userId']}")
    print("Access token was not printed.")


if __name__ == "__main__":
    main()

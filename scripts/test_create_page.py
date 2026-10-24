import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8005/api"


def req(method, path, data=None, token=None):
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None if data is None else json.dumps(data).encode()
    request = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, {"raw": raw}


def main():
    status, login = req("POST", "/auth/login", {"username": "admin", "password": "admin123"})
    print("login", status, login.get("success"))
    token = login.get("token")
    if not token:
        print(login)
        return

    status, pages = req("GET", "/auth/pages", token=token)
    print("existing SSH-like:")
    for p in pages.get("pages") or []:
        if "ssh" in (p.get("name") or "").lower() or "ssh" in (p.get("path") or "").lower():
            print(p)

    payload = {
        "name": "SSH",
        "path": "/ssh",
        "role_ids": [1],
        "icon": "FiTerminal",
        "parent_id": None,
    }
    status, created = req("POST", "/auth/pages", payload, token)
    print("create", status)
    print(json.dumps(created, indent=2))


if __name__ == "__main__":
    main()

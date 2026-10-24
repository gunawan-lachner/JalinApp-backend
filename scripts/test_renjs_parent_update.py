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
        return exc.code, json.loads(exc.read().decode())


def main():
    status, login = req("POST", "/auth/login", {"username": "admin", "password": "admin123"})
    print("login", status, login.get("success"), login.get("error"))
    token = login.get("token")
    if not token:
        return

    status, pages_resp = req("GET", "/auth/pages", token=token)
    print("pages status", status)
    items = pages_resp.get("pages") or []
    renjs = next((p for p in items if p.get("path") == "/renjs"), None)
    ide = next((p for p in items if p.get("path") == "/ide"), None)
    print("RENJS", renjs)
    print("IDE", ide)
    if not renjs or not ide:
        return

    # Clear parent first
    payload = {
        "name": renjs["name"],
        "path": renjs["path"],
        "role_ids": renjs.get("role_ids") or [1],
        "icon": renjs.get("icon"),
        "parent_id": None,
    }
    status, cleared = req("PUT", f"/auth/pages/{renjs['id_page']}", payload, token)
    print("clear parent", status, cleared.get("success"), cleared.get("page", {}).get("parent_id"), cleared.get("error"))

    payload["parent_id"] = ide["id_page"]
    status, updated = req("PUT", f"/auth/pages/{renjs['id_page']}", payload, token)
    print("set parent", status)
    print(json.dumps(updated, indent=2))


if __name__ == "__main__":
    main()

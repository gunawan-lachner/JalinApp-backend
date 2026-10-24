import json
import urllib.request

BASE = "http://127.0.0.1:8005/api"

login_req = urllib.request.Request(
    BASE + "/auth/login",
    data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(login_req) as resp:
    token = json.loads(resp.read().decode())["token"]

pages_req = urllib.request.Request(
    BASE + "/auth/pages",
    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    method="GET",
)
with urllib.request.urlopen(pages_req) as resp:
    body = resp.read().decode()

print(body)
data = json.loads(body)
for page in data.get("pages", []):
    if page.get("path") in ("/renjs", "/ide"):
        print("KEYS", sorted(page.keys()))
        print("PAGE", page)

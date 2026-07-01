import urllib.request, json

TOKEN = "efd8f35c-9034-45b5-a4ce-9f14d4861c46"
PROJECT_ID = "6aa35c0e-de2f-49e3-a221-e08fb413958f"
ENV_ID = "a05d576c-53a9-47d1-a46f-129afbed44f6"
SERVICE_ID = "1c82597d-3c26-48ed-9298-359f2d257b29"

body = json.dumps({
    "query": "mutation v($i:VariableUpsertInput!){variableUpsert(input:$i)}",
    "variables": {"i": {
        "projectId": PROJECT_ID, "environmentId": ENV_ID, "serviceId": SERVICE_ID,
        "name": "RAILWAY_API_TOKEN_TEST", "value": "ok"
    }}
}).encode()

req = urllib.request.Request(
    "https://backboard.railway.app/graphql/v2",
    data=body,
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}", "User-Agent": "Mozilla/5.0"}
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
        print(json.dumps(result))
except urllib.request.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:400])

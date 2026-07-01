import urllib.request, json

TOKEN = "1a0bab93-d8b0-4c70-a80f-3ca1bce2b02a"
PROJECT_ID = "c1883c58-a695-4ebf-a81f-8b2226056138"
SERVICE_ID = "1c82597d-3c26-48ed-9298-359f2d257b29"
ENV_ID = "2506b251-8842-4798-ad96-59bcafa1a9b4"

# Try to read Railway variables (to confirm SEED vars are set)
query = (
    "{ variables(projectId: \"%s\", environmentId: \"%s\", serviceId: \"%s\") }"
    % (PROJECT_ID, ENV_ID, SERVICE_ID)
)

req = urllib.request.Request(
    "https://backboard.railway.app/graphql/v2",
    data=json.dumps({"query": query}).encode(),
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TOKEN}",
        "User-Agent": "Mozilla/5.0",
    }
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
        # Show only SEED var names, not values (too long)
        if "data" in data and data["data"] and "variables" in data["data"]:
            vars_dict = data["data"]["variables"]
            seed_vars = {k: f"({len(v)} chars)" for k, v in vars_dict.items() if "SEED" in k}
            other_relevant = {k: v for k, v in vars_dict.items()
                              if k in ("RAILWAY_VOLUME_MOUNT_PATH", "BOT_DASHBOARD_URL", "RAILWAY_API_TOKEN")}
            print("SEED vars:", json.dumps(seed_vars, indent=2))
            print("Other relevant:", json.dumps(other_relevant, indent=2))
        else:
            print(json.dumps(data, indent=2))
except urllib.request.HTTPError as e:
    print("HTTP", e.code, e.read().decode()[:800])

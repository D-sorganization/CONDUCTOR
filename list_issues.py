"""List open issues and PRs from GitHub API using gh CLI."""
import json
import subprocess

# Call gh api with the URL as a single argument
result = subprocess.run(
    ["gh", "api", "repos/D-sorganization/Maxwell-Daemon/issues?state=open&per_page=100"],
    capture_output=True,
    text=True,
)
if result.returncode != 0:
    print("Error:", result.stderr)
    exit(1)

data = json.loads(result.stdout)

issues = [i for i in data if "pull_request" not in i]
prs = [i for i in data if "pull_request" in i]

print(f"Total open items: {len(data)}")
print(f"Open issues: {len(issues)}")
print(f"Open PRs: {len(prs)}")
print()

print("=== ISSUES ===")
for i in issues[:50]:
    labels = ",".join(label["name"] for label in i.get("labels", []))
    print(f"#{i['number']}: {i['title']}")
    print(f"   labels: {labels}")
    print(f"   url: {i['html_url']}")
    print()

print("=== PRs ===")
for p in prs[:50]:
    labels = ",".join(label["name"] for label in p.get("labels", []))
    print(f"#{p['number']}: {p['title']}")
    print(f"   labels: {labels}")
    print(f"   url: {p['html_url']}")
    print()

#!/usr/bin/env bash
# Create the GitHub repository via REST API (requires GH_TOKEN with repo scope).
set -euo pipefail

export GITHUB_REPO="${GITHUB_REPO:-Employee_Email_finder}"
export GITHUB_REPO_DESCRIPTION="${GITHUB_REPO_DESCRIPTION:-Employee Email Finder: domain resolution, email permutations, MX/SMTP validation without sending mail; Chrome extension + local Flask backend.}"
OWNER="${GITHUB_OWNER:-Priyansh-03}"

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "Error: set GH_TOKEN to a GitHub personal access token with \`repo\` scope." >&2
  exit 1
fi

BODY=$(REPO="$GITHUB_REPO" DESC="$GITHUB_REPO_DESCRIPTION" python3 <<'PY'
import json, os
print(json.dumps({
    "name": os.environ["REPO"],
    "description": os.environ["DESC"],
    "private": False,
    "has_issues": True,
    "has_projects": False,
    "has_wiki": False,
}))
PY
)

code=$(curl -sS -o /tmp/gh_create_repo.json -w "%{http_code}" \
  -X POST \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer ${GH_TOKEN}" \
  "https://api.github.com/user/repos" \
  -d "$BODY")

if [[ "$code" != "201" ]]; then
  echo "GitHub API returned HTTP $code" >&2
  cat /tmp/gh_create_repo.json >&2 || true
  exit 1
fi

echo "Created https://github.com/${OWNER}/${GITHUB_REPO}"

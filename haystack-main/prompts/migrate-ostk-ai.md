Update os-tack/ostk.ai mirror-release.yml to pull from os-tack/haystack instead of os-tack/haystack.

The file is at .github/workflows/mirror-release.yml in the os-tack/ostk.ai repo.

Current line:
  BASE="https://github.com/os-tack/haystack/releases/download/${VERSION}"

Replace with:
  BASE="https://github.com/os-tack/haystack/releases/download/${VERSION}"

Use gh api to update the file:
1. Get current file SHA: gh api repos/os-tack/ostk.ai/contents/.github/workflows/mirror-release.yml --jq '.sha'
2. Get current content: decode base64
3. Make replacement
4. Update via gh api PUT with new content + sha + commit message "chore: update haystack source to os-tack org"

Then trigger the v1.3.0 release mirror:
  gh workflow run mirror-release.yml --repo os-tack/ostk.ai -f version=v1.3.0

Wait for the workflow to complete and report the release URL.

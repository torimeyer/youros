import sys
from pathlib import Path

f = 'api/tests/test_agents.py'
path = Path(f)
content = path.read_text()
# Order matters: replace longer patterns first to avoid partial replacements
new_content = content.replace('mkdir(parents=True)', 'mkdir(parents=True, exist_ok=True)')
new_content = new_content.replace('mkdir()', 'mkdir(exist_ok=True)')
if content != new_content:
    path.write_text(new_content)
    print(f'Updated {f}')
else:
    print(f'No changes needed for {f}')

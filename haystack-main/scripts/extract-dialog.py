#!/usr/bin/env python3
"""Extract clean human/assistant dialog from a Claude Code JSONL transcript.

Usage: python3 extract-dialog.py <jsonl_path> [--format md|json] [--max-chars 500]
"""

import json
import sys
import re
import argparse

def clean_text(text: str) -> str:
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text, flags=re.DOTALL)
    text = re.sub(r'<local-command-caveat>.*?</local-command-caveat>', '', text, flags=re.DOTALL)
    text = re.sub(r'<local-command-stdout>.*?</local-command-stdout>', '', text, flags=re.DOTALL)
    text = re.sub(r'<command-\w+>.*?</command-\w+>', '', text, flags=re.DOTALL)
    return text.strip()

def extract_dialog(jsonl_path: str, max_chars: int = 500) -> list:
    dialog = []
    seen_uuids = set()

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            if entry.get('isSidechain'):
                continue
            if entry.get('type') == 'progress':
                continue

            msg = entry.get('message', {})
            role = msg.get('role')
            uuid = entry.get('uuid', '')

            if uuid in seen_uuids:
                continue
            seen_uuids.add(uuid)

            if role == 'user':
                content = msg.get('content', '')
                if isinstance(content, list):
                    texts = []
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            texts.append(block['text'])
                        elif isinstance(block, str):
                            texts.append(block)
                    content = '\n'.join(texts)

                content = clean_text(content)
                if content and len(content) > 5:
                    dialog.append({
                        'role': 'human',
                        'text': content[:max_chars] + ('...' if len(content) > max_chars else ''),
                        'ts': entry.get('timestamp', ''),
                    })

            elif role == 'assistant':
                content = msg.get('content', [])
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = ''
                    for block in content:
                        if isinstance(block, dict) and block.get('type') == 'text':
                            text = block['text']
                            break
                else:
                    continue

                text = clean_text(text)
                if text and len(text) > 5:
                    dialog.append({
                        'role': 'assistant',
                        'text': text[:max_chars] + ('...' if len(text) > max_chars else ''),
                        'ts': entry.get('timestamp', ''),
                    })

    return dialog

def format_markdown(dialog: list) -> str:
    lines = ["# Session Dialog\n"]
    for entry in dialog:
        ts = entry['ts'][:19] if entry['ts'] else ''
        role = '**Human**' if entry['role'] == 'human' else '**Assistant**'
        lines.append(f"### {role} ({ts})")
        lines.append(entry['text'])
        lines.append('')
    return '\n'.join(lines)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Extract dialog from Claude Code JSONL')
    parser.add_argument('jsonl_path', help='Path to JSONL transcript')
    parser.add_argument('--format', choices=['md', 'json'], default='md')
    parser.add_argument('--max-chars', type=int, default=500, help='Max chars per message')
    parser.add_argument('--output', '-o', help='Output file (default: stdout)')
    args = parser.parse_args()

    dialog = extract_dialog(args.jsonl_path, args.max_chars)

    if args.format == 'md':
        result = format_markdown(dialog)
    else:
        result = json.dumps(dialog, indent=2)

    if args.output:
        with open(args.output, 'w') as f:
            f.write(result)
        print(f"Wrote {len(dialog)} exchanges to {args.output}", file=sys.stderr)
    else:
        print(result)

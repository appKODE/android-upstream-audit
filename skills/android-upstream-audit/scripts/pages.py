#!/usr/bin/env python3
"""Downloads each documentation page once per run and serves quotes from the saved text.

    python3 pages.py get  --out DIR URL
    python3 pages.py grep --out DIR URL PATTERN [--context N]

`get` downloads URL with curl, unless this run already has it, converts the HTML to plain text
(one line per paragraph, one line per table row with cells joined by " | ") and saves it under
DIR/pages/. `grep` prints the lines of that text matching PATTERN (a regular expression,
case-insensitive), downloading the page first if needed.

Quotes for the edits file are copied from what `grep` prints. slice.py later checks each new
quote against the same saved text, without the network, so a quote that is not on the page
cannot reach the report.
"""
import argparse
import hashlib
import html
import html.parser
import json
import os
import re
import sys

import common

SKIP = {'script', 'style', 'noscript', 'svg', 'template', 'head'}
BLOCK = {'p', 'div', 'section', 'article', 'header', 'footer', 'li', 'ul', 'ol', 'dt', 'dd',
         'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'blockquote', 'table', 'thead', 'tbody',
         'caption', 'br', 'hr', 'main', 'nav', 'aside', 'figure', 'figcaption'}


class _Text(html.parser.HTMLParser):
    """Plain text of a page: a line per block, a line per table row."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines, self.line, self.row, self.cell, self.skip = [], [], None, None, 0

    def _flush(self):
        text = ' '.join(''.join(self.line).split())
        if text:
            self.lines.append(text)
        self.line = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag == 'tr':
            self._flush()
            self.row = []
        elif tag in ('td', 'th') and self.row is not None:
            self.cell = []
        elif tag in BLOCK and self.cell is None:
            self._flush()

    def handle_endtag(self, tag):
        if tag in SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if any(self.row):
                self.lines.append(' | '.join(self.row))
            self.row = None
        elif tag in BLOCK and self.cell is None:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        (self.cell if self.cell is not None else self.line).append(data)

    def text(self):
        self._flush()
        return '\n'.join(self.lines) + '\n'


def to_text(raw):
    parser = _Text()
    parser.feed(raw)
    return parser.text()


def json_text(raw):
    """An API answer (api.github.com) as lines a quote can be found in: each text field with its
    real line breaks. Kept as JSON, a release body is one line of escaped "\\r\\n", and a quote
    of its first sentence never matches. None when `raw` is not JSON."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    lines = []

    def walk(value, key=''):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and value.strip():
            text = value.replace('\r\n', '\n').replace('\r', '\n')
            lines.append(f'{key}: {text}' if '\n' not in text else f'{key}:\n{text}')
        elif value is not None and not isinstance(value, str):
            lines.append(f'{key}: {value}')
    walk(data)
    return '\n'.join(lines) + '\n'


def _index_path(out):
    return os.path.join(out, 'pages', 'index.json')


def load_index(out):
    path = _index_path(out)
    return common.load_json(path, 'the pages index') if os.path.exists(path) else {}


def page_text(out, url):
    """The saved text of URL, or None when this run has not downloaded it."""
    entry = load_index(out).get(url)
    if not entry or entry.get('status') != 'ok':
        return None
    with open(os.path.join(out, 'pages', entry['file']), encoding='utf-8') as f:
        return f.read()


def get(out, url):
    """Returns (path to the saved text, whether it was downloaded now)."""
    os.makedirs(os.path.join(out, 'pages'), exist_ok=True)
    index = load_index(out)
    entry = index.get(url)
    if entry and entry.get('status') == 'ok':
        return os.path.join(out, 'pages', entry['file']), False
    name = hashlib.sha1(url.encode()).hexdigest()[:12] + '.txt'
    try:
        raw = common.curl(url)
    except Exception as e:
        index[url] = {'status': 'failed', 'error': str(e)[:300]}
        _save_index(out, index)
        raise
    head = raw.lstrip()[:1]
    text = (to_text(raw) if '<html' in raw[:2000].lower() else
            (json_text(raw) if head in ('{', '[') else None) or raw)
    with open(os.path.join(out, 'pages', name), 'w', encoding='utf-8') as f:
        f.write(text)
    index[url] = {'status': 'ok', 'file': name, 'fetched': common.datetime.datetime.now(
        common.datetime.timezone.utc).strftime('%Y-%m-%dT%H:%MZ'), 'html_bytes': len(raw), 'text_bytes': len(text)}
    _save_index(out, index)
    return os.path.join(out, 'pages', name), True


def _save_index(out, index):
    with open(_index_path(out), 'w', encoding='utf-8') as f:
        json.dump(index, f, ensure_ascii=False, indent=1)


# Comparison signs and a trailing "+" carry the meaning of a requirement ("API level >= 37",
# "AGP 8.12.0+"), so they count; other punctuation, table bars and line breaks do not.
TOKEN = re.compile(r'[a-z0-9]+(?:\.[a-z0-9]+)*\+?|<=|>=|!=|<|>|=')
SIGNS = {'≥': '>=', '⩾': '>=', '≤': '<=', '⩽': '<=', '≠': '!='}


def tokens(text):
    """Words, version numbers and comparison signs, lower case."""
    text = html.unescape(text).lower()
    for sign, ascii_sign in SIGNS.items():
        text = text.replace(sign, ascii_sign)
    return ' '.join(TOKEN.findall(text))


def quote_found(claim, text):
    """True when every part of `claim` (parts separated by … or ...) appears in `text`, in order,
    word for word."""
    haystack = ' ' + tokens(text) + ' '
    pos = 0
    for part in re.split(r'…|\.\.\.', claim):
        needle = tokens(part)
        if not needle:
            continue
        at = haystack.find(' ' + needle + ' ', pos)
        if at < 0:
            return False
        pos = at + len(needle)
    return True


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='cmd', required=True)
    g = sub.add_parser('get')
    g.add_argument('--out', required=True)
    g.add_argument('url')
    s = sub.add_parser('grep')
    s.add_argument('--out', required=True)
    s.add_argument('url')
    s.add_argument('pattern')
    s.add_argument('--context', type=int, default=0)
    args = p.parse_args()

    try:
        path, fresh = get(args.out, args.url)
    except Exception as e:
        print(f'FAILED {args.url} — {e}')
        print('Fall back to WebFetch for this page and mark its quotes "unverified" in the edits file.')
        return 1
    with open(path, encoding='utf-8') as f:
        lines = f.read().split('\n')
    if args.cmd == 'get':
        print(f"{'downloaded' if fresh else 'already saved'}: {args.url} -> {path} ({len(lines)} lines)")
        return 0
    try:
        pattern = re.compile(args.pattern, re.I)
    except re.error as e:
        print(f'bad pattern {args.pattern!r}: {e}')
        return 1
    hits = [i for i, line in enumerate(lines) if pattern.search(line)]
    print(f"{'downloaded' if fresh else 'already saved'}: {args.url} — {len(hits)} matching lines")
    shown = set()
    for i in hits[:40]:
        for j in range(max(0, i - args.context), min(len(lines), i + args.context + 1)):
            if j not in shown:
                shown.add(j)
                print(f"{j + 1:5}{'>' if j == i else ' '} {lines[j]}")
    if len(hits) > 40:
        print(f'... {len(hits) - 40} more — narrow the pattern')
    if not hits:
        print('no line matches — the page does not say it, or says it in other words')
    return 0


if __name__ == '__main__':
    sys.exit(main())

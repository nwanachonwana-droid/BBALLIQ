#!/usr/bin/env python3
"""
Convert a Design Component artboard (.dc.html) into a plain, standalone
HTML page that works on any static host.

The artboard format depends on a runtime only claude.ai serves:
  <script src="./support.js">   the runtime itself
  <x-dc> ... </x-dc>            the component root
  <helmet> ... </helmet>        head content, hoisted by the runtime
  {{hole}}, <sc-for>, <sc-if>   template directives
  <script type="text/x-dc">     the component's logic class

Dropped onto GitHub Pages as-is, every one of those renders as nothing.
This strips the wrapper and hoists the helmet into a real <head>, which
is all three of the article pages need -- they carry no holes and no
loops, so the conversion is lossless and mechanical.

The board page has tables, tabs and state, so its logic is rewritten by
hand in plain JS rather than guessed at here.

usage
  python3 convert.py Research.dc.html research.html
"""
import os, re, sys

LINKS = {
    'Main.dc.html': 'index.html',
    'Research.dc.html': 'research.html',
    'Minutes.dc.html': 'minutes.html',
    'Misses.dc.html': 'misses.html',
}


def convert(src, dst):
    s = open(src).read()

    # 1. pull the helmet out; it holds the fonts link and base styles
    m = re.search(r'<helmet>(.*?)</helmet>', s, re.S)
    helmet = m.group(1).strip() if m else ''
    s = re.sub(r'<helmet>.*?</helmet>', '', s, flags=re.S)

    # 2. drop the runtime script tag
    s = re.sub(r'\s*<script src="\./support\.js"></script>', '', s)

    # 3. drop the component logic block
    s = re.sub(r'<script type="text/x-dc".*?</script>', '', s, flags=re.S)

    # 4. unwrap the component root
    s = s.replace('<x-dc>', '').replace('</x-dc>', '')

    # 5. hoist the helmet into the real head
    s = s.replace('</head>', helmet + '\n</head>')

    # 6. repoint inter-page links at the deployed filenames
    for old, new in LINKS.items():
        s = s.replace(f'href="{old}"', f'href="{new}"')

    # 7. a viewport meta, since these are now real pages on phones
    if 'name="viewport"' not in s:
        s = s.replace('<meta charset="utf-8">',
                      '<meta charset="utf-8">\n'
                      '<meta name="viewport" content="width=device-width, '
                      'initial-scale=1">')

    # tidy the blank lines the removals leave behind
    s = re.sub(r'\n{3,}', '\n\n', s)
    open(dst, 'w').write(s)

    leftover = []
    for pat, label in ((r'\{\{', '{{holes}}'), (r'<sc-(for|if)', 'sc- tags'),
                       (r'<x-dc', 'x-dc'), (r'<helmet', 'helmet'),
                       (r'support\.js', 'support.js'),
                       (r'data-dc-script', 'dc script')):
        if re.search(pat, s):
            leftover.append(label)
    return len(s), leftover


if __name__ == '__main__':
    if len(sys.argv) == 3:
        n, bad = convert(sys.argv[1], sys.argv[2])
        print(f'{sys.argv[1]} -> {sys.argv[2]}  {n} bytes'
              + (f'   LEFTOVER: {", ".join(bad)}' if bad else '   clean'))
    else:
        for src in ('Research.dc.html', 'Minutes.dc.html', 'Misses.dc.html'):
            if not os.path.exists(src):
                print(f'{src} missing')
                continue
            dst = LINKS[src]
            n, bad = convert(src, dst)
            print(f'{src:22} -> {dst:16} {n:>7} bytes'
                  + (f'   LEFTOVER: {", ".join(bad)}' if bad else '   clean'))

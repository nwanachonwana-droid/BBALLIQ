#!/usr/bin/env python3
"""Does each stored result follow from its own line and actual?

An independent check of settle(). slate.py audit re-derives results
from the box score using the SAME settle() the grader used, so a sign
error in settle passes its own audit. This does no re-derivation: it
takes the line, the side and the actual already written on the row and
asks whether the recorded win/loss follows from them.

  under 9.5 with an actual of 9 is a win. If the row says loss, the
  grader is wrong. If the row says win and the record still counts it
  against you, the record is wrong. Different bugs, different fixes.

usage: python3 check_grades.py [YYYYMMDD]
"""
import os, sys, json

SLATE = os.path.expanduser('~/kalshi_data/slate.jsonl')
day = sys.argv[1] if len(sys.argv) > 1 else None

rows = []
for ln in open(SLATE):
    ln = ln.strip()
    if ln:
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
rows = [r for r in rows if r.get('result') in ('win', 'loss', 'push')]
if day:
    rows = [r for r in rows if r.get('day') == day]

print(f'{len(rows)} settled row(s)' + (f' on {day}' if day else ''))
print(f"\n  {'DAY':9}{'PLAYER':20}{'MKT':10}{'SIDE':6}{'LINE':>6}"
      f"{'ACTUAL':>7}{'STORED':>8}{'SHOULD':>8}{'GR':>4}  AGREE")
bad = []
for r in sorted(rows, key=lambda r: (r.get('grade') or 'Z', r['day'])):
    a, ln_ = r.get('actual'), r.get('line')
    if a is None or ln_ is None:
        continue
    if a == ln_:
        should = 'push'
    elif r['side'] == 'over':
        should = 'win' if a > ln_ else 'loss'
    else:
        should = 'win' if a < ln_ else 'loss'
    agree = (should == r['result'])
    if not agree:
        bad.append(r)
    print(f"  {r['day']:9}{str(r.get('subject'))[:19]:20}"
          f"{str(r.get('market'))[:9]:10}{r['side']:6}{ln_:>6.1f}"
          f"{a:>7}{r['result']:>8}{should:>8}"
          f"{(r.get('grade') or '-'):>4}  {'ok' if agree else 'MISMATCH'}")

print()
if bad:
    print(f'{len(bad)} row(s) where the recorded result does not follow from\n'
          f'the line and the actual. That is a grader bug, not a model one.')
else:
    print('Every recorded result follows from its own line and actual.\n'
          'So the grading is self-consistent: if a grade band is losing,\n'
          'the rows really did lose, and the question is the ACTUAL --\n'
          'whether the box-score number written on the row is the right\n'
          'one. Run: python3 ~/Downloads/slate.py audit')

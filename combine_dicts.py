#!/usr/bin/env python3
"""Build dictionary.jsonl from CC-CEDICT (中→英) and ECDICT (英→中).

Output is JSONL with short keys and omitted empties, because the whole file is
downloaded and parsed by the client on first run:

    i  id (int)                    f  frequency rank, 1 = most common (omitted if unknown)
    t  0 = zh-en, 1 = en-zh        g  exam tags, e.g. "cet4 ielts"   (en-zh)
    w  headword                    c  Collins star rating 1-5        (en-zh)
    s  simplified   (zh-en)        h  phonetic                       (en-zh)
    p  pinyin       (zh-en)        z  Chinese translations           (en-zh)
    d  definitions                 x  inflections                    (en-zh)

ECDICT is filtered to entries carrying a corpus rank or an exam-list tag.
Including all 768k translated rows would push the client payload past 80 MB;
the ~59k kept here cover common vocabulary and every learner exam list, and
the app falls back to online translation for anything missing.
"""

import csv
import json
import math
import os
import re
import sys
from collections import Counter

CEDICT_FILE = 'cedict_ts.u8'
ECDICT_FILE = 'ecdict.csv'
OUTPUT_FILE = 'dictionary.jsonl'

CEDICT_RE = re.compile(r'^(\S+)\s+(\S+)\s+\[(.*?)\]\s+/(.*)/\s*$')
# CJK Unified, Extension A, and Compatibility Ideographs. Rare Extension B
# characters fall outside this on purpose: they score 0 and sort last, which
# is where entries like the biang-biang noodle glyphs belong.
CJK_RE = re.compile('[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]')

csv.field_size_limit(10 ** 7)


def die(msg):
    print(f'❌ {msg}', file=sys.stderr)
    sys.exit(1)


def as_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def read_cedict(path):
    """Parse CC-CEDICT into raw tuples."""
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            m = CEDICT_RE.match(line)
            if not m:
                continue
            trad, simp, pinyin, meanings = m.groups()
            defs = [d.strip() for d in meanings.split('/') if d.strip()]
            if not defs:
                continue
            rows.append((trad, simp, pinyin, defs))
    return rows


CJK_RUN_RE = re.compile('[一-鿿㐀-䶿豈-﫿]+')

# Glosses that describe an entry rather than define it. These are real entries
# worth keeping, but they should lose ties to the substantive homograph.
META_GLOSS_RE = re.compile(
    r'^(surname\b|variant of\b|old variant of\b|used in\b|see \b|see also\b|abbr\.)', re.I)


def chinese_ranks_from_ecdict(path):
    """Map Chinese word -> best English corpus rank that translates to it.

    CC-CEDICT ships no frequency data, but ECDICT does, and its translations
    point the other way: "I" is BNC rank 11 and translates to 我, so 我 is a
    rank-11-ish word. Inverting that gives real corpus-grounded frequencies for
    the Chinese side without pulling in any external word list.

    A word takes the best (lowest) rank of any English headword translating to
    it, since a word is as common as its most common use.
    """
    best = {}
    with open(path, encoding='utf-8') as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if len(row) < 11:
                continue
            zh = row[3].strip()
            if not zh:
                continue
            corpus = [r for r in (as_int(row[8]), as_int(row[9])) if r > 0]
            if not corpus:
                continue
            rank = min(corpus)
            for token in CJK_RUN_RE.findall(zh):
                # Long runs are glosses ("在...期间"), not headwords.
                if 1 <= len(token) <= 4 and rank < best.get(token, 1 << 30):
                    best[token] = rank
    return best


def rank_cedict(rows, zh_ranks):
    """Order CC-CEDICT entries by frequency, best evidence first.

    Tier 1: words ECDICT gives a real corpus rank for, ordered by that rank.
    Tier 2: everything else, ordered by a fallback heuristic and placed strictly
            below tier 1 so a guess can never outrank measured data.

    The fallback scores a word by its *rarest* character rather than the mean:
    a word is only as common as the least common character in it. Using the mean
    was the earlier mistake — it let compounds of productive characters (大, 人,
    子) outrank genuinely common standalone words.
    """
    doc_freq = Counter()
    for trad, simp, _, _ in rows:
        for ch in set(trad) | set(simp):
            if CJK_RE.match(ch):
                doc_freq[ch] += 1

    measured, guessed = [], []
    for idx, (trad, simp, _, defs) in enumerate(rows):
        # Homographs all match the same headword string and so inherit the same
        # corpus rank. Break that tie against cross-reference and name entries so
        # 那 opens "that" rather than "surname Na".
        meta = 1 if META_GLOSS_RE.match(defs[0]) else 0

        known = zh_ranks.get(simp) or zh_ranks.get(trad)
        if known is not None:
            measured.append((known, meta, len(trad), idx))
            continue

        chars = [c for c in trad if CJK_RE.match(c)]
        if not chars:
            score = -1e9
        else:
            score = min(math.log(doc_freq.get(c, 1) + 1) for c in chars)
            score -= 0.35 * (len(chars) - 1)          # longer = rarer
            if len(chars) != len(trad):
                score -= 3.0                          # mixed script: 大V, K人, e人
            score += 0.10 * math.log(len(defs) + 1)   # more senses = better attested
        guessed.append((-score, meta, len(trad), idx))

    measured.sort()
    guessed.sort()

    ranks = [0] * len(rows)
    n = 0
    for entry in measured:
        n += 1
        ranks[entry[-1]] = n
    for entry in guessed:
        n += 1
        ranks[entry[-1]] = n
    return ranks, len(measured)


def main():
    missing = [p for p in (CEDICT_FILE, ECDICT_FILE) if not os.path.exists(p)]
    if missing:
        die(f'Missing required input(s): {", ".join(missing)}\n'
            f'   Refusing to write a partial dictionary. '
            f'(This is exactly how the ECDICT half went silently absent before.)')

    uid = 0
    n_zh = n_en = 0

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as out:

        # ── CC-CEDICT: Chinese → English ────────────────────────────────────
        print(f'Parsing {CEDICT_FILE}…')
        rows = read_cedict(CEDICT_FILE)
        if not rows:
            die(f'{CEDICT_FILE} produced no entries — is the format right?')
        print(f'   {len(rows):,} entries; deriving frequency ranks from ECDICT…')
        zh_ranks = chinese_ranks_from_ecdict(ECDICT_FILE)
        ranks, n_measured = rank_cedict(rows, zh_ranks)
        print(f'   {len(zh_ranks):,} Chinese words carry a corpus rank; '
              f'{n_measured:,}/{len(rows):,} entries matched '
              f'({100 * n_measured / len(rows):.0f}%), rest ranked heuristically')

        for (trad, simp, pinyin, defs), rank in zip(rows, ranks):
            uid += 1
            n_zh += 1
            e = {'i': uid, 't': 0, 'w': trad, 'p': pinyin, 'd': defs, 'f': rank}
            if simp and simp != trad:
                e['s'] = simp
            out.write(json.dumps(e, ensure_ascii=False, separators=(',', ':')) + '\n')
        print(f'✅ CC-CEDICT: {n_zh:,} entries')

        # ── ECDICT: English → Chinese ───────────────────────────────────────
        print(f'Parsing {ECDICT_FILE}…')
        skipped_no_zh = skipped_obscure = 0
        with open(ECDICT_FILE, encoding='utf-8') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header or header[0].strip().lower() != 'word':
                die(f'{ECDICT_FILE} header looks wrong: {header[:4] if header else None}')

            for row in reader:
                if len(row) < 11:
                    continue
                word = row[0].strip()
                zh = row[3].strip()
                if not word or not zh:
                    skipped_no_zh += 1
                    continue

                collins, oxford = as_int(row[5]), as_int(row[6])
                tags = row[7].strip()
                bnc, coca = as_int(row[8]), as_int(row[9])

                corpus = [r for r in (bnc, coca) if r > 0]
                if not corpus and not tags and not collins and not oxford:
                    skipped_obscure += 1
                    continue

                uid += 1
                n_en += 1
                e = {'i': uid, 't': 1, 'w': word}

                if corpus:
                    e['f'] = min(corpus)
                elif collins:
                    # Collins 5 ≈ very common; map onto the same rank scale.
                    e['f'] = 60000 - collins * 10000
                else:
                    e['f'] = 60000

                phon = row[1].strip()
                if phon:
                    e['h'] = phon

                en_def = row[2].strip().replace('\\n', '\n')
                defs = [d.strip() for d in en_def.split('\n') if d.strip()][:6]
                if defs:
                    e['d'] = defs

                e['z'] = [t.strip() for t in zh.replace('\\n', '\n').split('\n') if t.strip()]

                exch = row[10].strip()
                if exch:
                    e['x'] = exch
                if tags:
                    e['g'] = tags
                if collins:
                    e['c'] = collins

                out.write(json.dumps(e, ensure_ascii=False, separators=(',', ':')) + '\n')

        print(f'✅ ECDICT: {n_en:,} entries kept '
              f'({skipped_obscure:,} unranked/untagged skipped, '
              f'{skipped_no_zh:,} with no Chinese translation)')

    size_mb = os.path.getsize(OUTPUT_FILE) / 1e6
    print(f'\n🎉 {OUTPUT_FILE}: {uid:,} entries, {size_mb:.1f} MB '
          f'({n_zh:,} 中→英 + {n_en:,} 英→中)')


if __name__ == '__main__':
    main()

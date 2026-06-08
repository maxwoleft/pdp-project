"""Enrich profile.translation.keywords з distinctive words з sample names усіх ckeys.

Для кожного profile:
  1. Витягає UA-частину імен з МIN(service.name) для кожного ckey.
  2. Token-izуе → фільтрує stopwords + generic terms.
  3. Додає unique tokens до keywords (max +20 per profile).
  4. Re-embed translation.

Це покращує AI search для niche-термінів типу "афрокудрі", "балаяж", "ботокс
для волосся", специфічних brand sub-products, тощо.

Запуск:
    python -m scripts.enrich_profile_keywords --country ua --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import re
from collections import Counter

from sqlalchemy import text

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("enrich")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

STOPWORDS = {
    "та","і","в","у","з","за","на","для","між","під","над","до","від","без","по",
    "майстер","топ","арт","барбер","junior","юніор","top","art","master",
    "грн","uah","pln","gbp","зл","фунт","mln","шт","one","нve","два","три","чотири",
    "довжина","довжини","довжину","коротке","середнє","довге","extra","long","medium","short",
    "хв","хвилин","год","година","мин",
    "1","2","3","4","5","6","7","8","9","10","11","12","15","20","30","45","60","90","120",
    "I","II","III","IV","V",
    "+","/","-","–","—","|",
    "тип","рівень","комплекс","сет","версія","серія","процедура",
    "звичайн","звичайний","звичайне","звичайна",
    "rus","ua","en","pl","перший","друге","нve",
    "перейти","до","також","потім","зараз",
    "посл","послуги","послуга","сервіс",
    "ru","english",
}

# Generic кейзи що повторюються у багатьох — не додаємо як keyword:
SKIP_GENERIC = {
    "догляд","укладка","фарбування","стрижка","масаж","манікюр","педикюр",
    "брови","вії","нігті","нігтів","нігтя","волосся","голови","шкіри",
    "та","і","для","без","на","за","до","від","між","під","над","по",
}

_UA_PART_RE = re.compile(r"(?:^|/)\s*UA\s+([^/]+?)(?=\s*/|$)", re.IGNORECASE)
_WORD_RE = re.compile(r"[А-Яа-яІіЇїЄєҐґʼ'\w]+", re.UNICODE)


def extract_ua(name: str) -> str:
    if not name:
        return ""
    m = _UA_PART_RE.search(name)
    return (m.group(1) if m else name).strip()


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in _WORD_RE.findall(text) if len(t) >= 3]


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--language", default="uk")
    parser.add_argument("--max-add", type=int, default=20)
    args = parser.parse_args()

    embedder = OpenAIEmbedder()
    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            profiles = (await session.execute(text("""
                SELECT sp.id, sp.name, sp.canonical_keys,
                       t.id AS t_id, t.keywords, t.short_description, t.detailed_description,
                       t.addresses_problems, t.target_audience, t.benefits
                FROM public.service_profile sp
                JOIN public.service_profile_translation t
                  ON t.profile_id = sp.id AND t.language = :lang
                WHERE sp.country = :c
            """), {"c": args.country, "lang": args.language})).all()
            log.info("[%s] profiles: %d", args.country, len(profiles))

            updates = []
            for pid, pname, ckeys, t_id, kw, sd, dd, ap, ta, b in profiles:
                ckeys_list = list(ckeys or [])
                if not ckeys_list:
                    continue
                # Get sample names for all ckeys
                samples_rows = (await session.execute(text(f"""
                    SELECT canonical_key, MIN(name)
                    FROM {args.country}.service
                    WHERE archive=false AND canonical_key = ANY(:keys)
                    GROUP BY canonical_key
                """), {"keys": ckeys_list})).all()
                samples = [extract_ua(s) for _, s in samples_rows if s]

                # Token frequency
                token_counter = Counter()
                for s in samples:
                    for tok in tokenize(s):
                        if tok in STOPWORDS or tok in SKIP_GENERIC:
                            continue
                        token_counter[tok] += 1

                # Pick top tokens — distinct (appear in ≤50% of ckeys = специфічні varianti)
                # Plus those що appear в >1 ckey (повторюються — стабільні concepts)
                niche_tokens = []
                for tok, cnt in token_counter.most_common(60):
                    if cnt >= 2:  # at least 2 ckeys mention it
                        niche_tokens.append(tok)
                    if len(niche_tokens) >= args.max_add:
                        break

                existing_kw = [k.lower() for k in (kw or [])]
                new_kw = list(kw or [])
                added = 0
                for tok in niche_tokens:
                    if tok in existing_kw:
                        continue
                    new_kw.append(tok)
                    added += 1
                if added == 0:
                    continue

                updates.append({
                    "t_id": str(t_id), "pname": pname, "added": added,
                    "kw": new_kw, "sd": sd or "", "dd": dd or "",
                    "ap": list(ap or []), "ta": list(ta or []), "b": list(b or []),
                })

            log.info("[%s] profiles needing enrichment: %d", args.country, len(updates))
            if not args.apply:
                for u in updates[:10]:
                    log.info("  %s: +%d keywords", u["pname"], u["added"])
                return

            for u in updates:
                await session.execute(text("""
                    UPDATE public.service_profile_translation
                    SET keywords = CAST(:kw AS json), updated_at = NOW()
                    WHERE id = :t_id
                """), {"kw": json.dumps(u["kw"], ensure_ascii=False), "t_id": u["t_id"]})
            await session.commit()
            log.info("[%s] keywords updated for %d translations.", args.country, len(updates))

            # Re-embed
            done = 0
            for u in updates:
                parts = []
                if u["sd"]:
                    parts.append(u["sd"])
                if u["dd"]:
                    parts.append(u["dd"])
                if u["ap"]:
                    parts.append("Problems: " + ", ".join(u["ap"]))
                if u["ta"]:
                    parts.append("For: " + ", ".join(u["ta"]))
                if u["b"]:
                    parts.append("Benefits: " + ", ".join(u["b"]))
                if u["kw"]:
                    parts.append("Keywords: " + ", ".join(u["kw"]))
                emb_text = " | ".join(parts)
                if not emb_text.strip():
                    continue
                try:
                    vec = await embedder.embed(emb_text)
                    await session.execute(text("""
                        UPDATE public.service_profile_translation
                        SET embedding = :v, updated_at = NOW()
                        WHERE id = :tid
                    """), {"v": str(vec), "tid": u["t_id"]})
                    done += 1
                except Exception as exc:
                    log.warning("embed fail %s: %s", u["pname"], exc)
            await session.commit()
            log.info("[%s] Re-embedded %d.", args.country, done)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())

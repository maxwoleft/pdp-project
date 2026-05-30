"""Auto-partial-matching: для missing canonical_keys підбирає existing profile
через token overlap з profile.canonical_keys[]. Заповнює key_descriptions з sample names.
Логує дії у .logs/auto_link/{timestamp}.json.

Запуск:
    python -m scripts.auto_link_missing_keys --country gb --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from collections import Counter
from pathlib import Path

from sqlalchemy import text

from app.infrastructure.db.session import build_engine, build_session_factory


# Stop tokens (без сенсу для матчингу — короткі, числові, generic)
STOP_TOKENS = {
    "i", "ta", "z", "v", "do", "od", "vid", "u", "po", "na", "dlya", "abo",
    "ne", "bez", "ta", "1", "2", "3", "4", "5", "6", "7", "8", "9", "10",
    "mm", "g", "kg", "hv", "min", "sec",
}

LOG_DIR = Path(".logs/auto_link")


def tokenize(key: str) -> set[str]:
    """Tokenize canonical_key на set of meaningful tokens."""
    tokens = re.split(r"[_\s\-]+", key.lower())
    return {t for t in tokens if t and t not in STOP_TOKENS and len(t) >= 3}


def extract_ua_name(sample: str) -> str:
    """Витягує UA частину з 'EN ... / UA ... / RUS ...' формату."""
    if not sample:
        return ""
    parts = [p.strip() for p in sample.split("/")]
    for p in parts:
        if p.startswith("UA "):
            return p[3:].strip()
    return sample.strip()


LENGTH_PATTERNS = re.compile(
    r"\((Short|Medium|Long|Extra Long|Tailbone length|Коротке волосся|Середнє волосся|Довге волосся|Дуже довге волосся|Довжина до куприка|Short & Medium|Bob)\)",
    flags=re.IGNORECASE,
)


def gen_key_description(sample_name: str) -> str:
    """Generate key_description as expert intent-detection context for AI.
    Описує В ЯКИХ ВИПАДКАХ ПРОПОНУВАТИ — для виявлення потреби клієнта."""
    ua = extract_ua_name(sample_name)
    if not ua:
        return ""

    is_top = bool(re.search(r"\bТОП\b", ua))
    length_match = LENGTH_PATTERNS.search(ua)
    length = length_match.group(1) if length_match else ""

    clean = LENGTH_PATTERNS.sub("", ua)
    clean = re.sub(r"\s+ТОП\s*", " ", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s{2,}", " ", clean).strip().rstrip(".")

    parts = [p.strip() for p in clean.split("+") if p.strip()]

    if len(parts) >= 2:
        primary, secondary = parts[0], parts[1]
        rest = parts[2:]
        ctx = (
            f"Пропонувати коли клієнт виявляє потребу у '{primary}' "
            f"і одночасно (або у розмові згадує) потребу у '{secondary}'"
        )
        if rest:
            ctx += f" та '{', '.join(rest)}'"
        ctx += (
            ". Доречно як економія часу і грошей — одна процедура замість декількох візитів. "
            "Виявити через: подвійний запит у тій же розмові, обмежений час, бажання комплексу."
        )
    else:
        ctx = (
            f"Пропонувати коли клієнт виявляє потребу саме у послузі '{clean}'. "
            "Виявити через: прямий запит, опис задачі що відповідає цій конкретній послузі."
        )

    if length:
        ctx += f" Доречно для клієнтів з довжиною волосся '{length}' — підтвердити уточнюючим питанням про довжину."
    if is_top:
        ctx += (
            " Преміум формат ТОП — пропонувати клієнтам, які виявляють потребу у найвищій якості: "
            "запитують про досвід майстра, ідуть на важливу подію, готові інвестувати у преміум."
        )

    return ctx


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--min-overlap", type=int, default=2,
                        help="Min meaningful token overlap to auto-link (default 2)")
    args = parser.parse_args()

    engine = build_engine()
    factory = build_session_factory(engine)
    log_entries: list[dict] = []

    try:
        async with factory() as session:
            # Missing keys + sample names
            r = await session.execute(text(f"""
                SELECT s.canonical_key, MIN(s.name) AS sample, COUNT(*) AS svc
                FROM {args.country}.service s
                WHERE s.archive=false AND s.profile_id IS NULL AND s.canonical_key IS NOT NULL
                GROUP BY s.canonical_key
                ORDER BY svc DESC
            """))
            missing = [(row[0], row[1], row[2]) for row in r.all()]
            print(f"Missing keys: {len(missing)}")

            # Profile token index: profile_id → set of all tokens у його keys
            pr = await session.execute(text("""
                SELECT id, name, canonical_keys FROM public.service_profile WHERE country = :c
            """), {"c": args.country})
            profile_tokens: list[tuple[str, str, set[str]]] = []
            for pid, pname, ck in pr.all():
                all_tokens: set[str] = set()
                for k in (ck or []):
                    all_tokens |= tokenize(k)
                profile_tokens.append((pid, pname, all_tokens))

            matched = 0
            for ckey, sample, svc_count in missing:
                ckey_tokens = tokenize(ckey)
                if not ckey_tokens:
                    continue
                best_pid = None
                best_pname = None
                best_score = 0
                for pid, pname, ptokens in profile_tokens:
                    overlap = len(ckey_tokens & ptokens)
                    if overlap > best_score:
                        best_score = overlap
                        best_pid = pid
                        best_pname = pname

                if best_score < args.min_overlap:
                    log_entries.append({
                        "canonical_key": ckey,
                        "sample_name": sample,
                        "service_count": svc_count,
                        "country": args.country,
                        "action": "skipped",
                        "reason": f"overlap={best_score} < min={args.min_overlap}",
                    })
                    continue

                desc = gen_key_description(sample)
                log_entries.append({
                    "canonical_key": ckey,
                    "sample_name": sample,
                    "service_count": svc_count,
                    "country": args.country,
                    "action": "linked",
                    "profile_id": str(best_pid),
                    "profile_name": best_pname,
                    "overlap_score": best_score,
                    "key_description": desc,
                })
                matched += 1
                print(f"  + [{best_score}] {ckey[:50]:50s} → {best_pname}")

                if args.apply:
                    # Add key + description через merge
                    await session.execute(text("""
                        UPDATE public.service_profile
                        SET canonical_keys = CASE
                            WHEN canonical_keys @> CAST(:kj AS jsonb) THEN canonical_keys
                            ELSE canonical_keys || CAST(:kj AS jsonb)
                        END,
                        key_descriptions = COALESCE(key_descriptions, '{}'::jsonb) || CAST(:dj AS jsonb)
                        WHERE id = :pid
                    """), {
                        "kj": json.dumps([ckey]),
                        "dj": json.dumps({ckey: desc}),
                        "pid": str(best_pid),
                    })
                    # Link services
                    await session.execute(text(f"""
                        UPDATE {args.country}.service SET profile_id = :pid
                        WHERE archive=false AND canonical_key = :k AND profile_id IS NULL
                    """), {"pid": str(best_pid), "k": ckey})

            if args.apply:
                await session.commit()

            # Save log
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            log_file = LOG_DIR / f"{args.country}_{ts}.json"
            with open(log_file, "w") as f:
                json.dump({
                    "timestamp": ts,
                    "country": args.country,
                    "applied": args.apply,
                    "matched": matched,
                    "skipped": len(missing) - matched,
                    "entries": log_entries,
                }, f, ensure_ascii=False, indent=2)
            print(f"\nDONE: matched={matched}, skipped={len(missing) - matched}")
            print(f"Log: {log_file}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())

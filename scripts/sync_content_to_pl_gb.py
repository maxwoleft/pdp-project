"""Sync aggregated UA content → PL/GB profiles з однаковими name.

UA уже має 488 per-ckey overrides, aggregated до profile-level. PL/GB мають
лише template defaults (3-4 items). Цей script:
  1. Для кожного PL/GB profile name знаходить UA profile з тим же name.
  2. Копіює addresses_problems/target_audience/benefits/keywords/cross_sell з UA.
  3. Зберігає sales_pitch=NULL (cleared).
  4. Re-embed translation.

Якщо немає UA-аналога — лишає поточний template default.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import text

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("sync_content")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, choices=["pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--language", default="uk")
    args = parser.parse_args()

    embedder = OpenAIEmbedder()
    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            # UA reference content indexed by lowercase name
            ua_rows = (await session.execute(text("""
                SELECT sp.name,
                       t.addresses_problems, t.target_audience, t.benefits,
                       t.keywords, t.cross_sell
                FROM public.service_profile sp
                JOIN public.service_profile_translation t
                  ON t.profile_id = sp.id AND t.language = :lang
                WHERE sp.country = 'ua'
            """), {"lang": args.language})).all()
            ua_by_name: dict[str, tuple] = {}
            for name, ap, ta, b, kw, cs in ua_rows:
                ua_by_name[(name or "").strip().lower()] = (ap, ta, b, kw, cs)
            log.info("UA reference profiles: %d", len(ua_by_name))

            target_rows = (await session.execute(text("""
                SELECT sp.id, sp.name, t.id AS t_id,
                       t.short_description, t.detailed_description,
                       t.addresses_problems, t.target_audience, t.benefits,
                       t.keywords, t.cross_sell
                FROM public.service_profile sp
                JOIN public.service_profile_translation t
                  ON t.profile_id = sp.id AND t.language = :lang
                WHERE sp.country = :c
                ORDER BY sp.name
            """), {"c": args.target, "lang": args.language})).all()
            log.info("[%s] profiles: %d", args.target, len(target_rows))

            matched = 0
            unmatched: list[str] = []
            updates: list[dict] = []
            for pid, pname, t_id, sd, dd, ap, ta, b, kw, cs in target_rows:
                key = (pname or "").strip().lower()
                ref = ua_by_name.get(key)
                if ref:
                    ua_ap, ua_ta, ua_b, ua_kw, ua_cs = ref
                    new_ap = _dedup(list(ap or []) + list(ua_ap or []))
                    new_ta = _dedup(list(ta or []) + list(ua_ta or []))
                    new_b = _dedup(list(b or []) + list(ua_b or []))
                    new_kw = _dedup(list(kw or []) + list(ua_kw or []))
                    new_cs = _dedup(list(cs or []) + list(ua_cs or []))
                    matched += 1
                else:
                    new_ap = list(ap or [])
                    new_ta = list(ta or [])
                    new_b = list(b or [])
                    new_kw = list(kw or [])
                    new_cs = list(cs or [])
                    unmatched.append(pname)
                updates.append({
                    "t_id": str(t_id), "pname": pname,
                    "sd": sd or "", "dd": dd or "",
                    "ap": new_ap, "ta": new_ta, "b": new_b,
                    "kw": new_kw, "cs": new_cs,
                })

            log.info("[%s] matched UA: %d, unmatched: %d", args.target, matched, len(unmatched))
            if unmatched[:15]:
                log.info("[%s] unmatched sample: %s", args.target, ", ".join(unmatched[:15]))

            if not args.apply:
                log.info("DRY RUN.")
                return

            for u in updates:
                await session.execute(text("""
                    UPDATE public.service_profile_translation SET
                      addresses_problems = CAST(:ap AS json),
                      target_audience = CAST(:ta AS json),
                      benefits = CAST(:b AS json),
                      keywords = CAST(:kw AS json),
                      cross_sell = CAST(:cs AS json),
                      sales_pitch = NULL,
                      updated_at = NOW()
                    WHERE id = :t_id
                """), {
                    "t_id": u["t_id"],
                    "ap": json.dumps(u["ap"], ensure_ascii=False),
                    "ta": json.dumps(u["ta"], ensure_ascii=False),
                    "b": json.dumps(u["b"], ensure_ascii=False),
                    "kw": json.dumps(u["kw"], ensure_ascii=False),
                    "cs": json.dumps(u["cs"], ensure_ascii=False),
                })
            await session.commit()
            log.info("[%s] UPDATED %d translations.", args.target, len(updates))

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
            log.info("[%s] Re-embedded %d.", args.target, done)
    finally:
        await engine.dispose()


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for it in items:
        if not it:
            continue
        k = it.strip().lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(it.strip())
    return out


if __name__ == "__main__":
    asyncio.run(amain())

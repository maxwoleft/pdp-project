"""Aggregate per-ckey overrides up to profile-level translation.

Для кожного profile:
  1. Збирає addresses_problems / target_audience / benefits / keywords / cross_sell
     з translation defaults + усіх ckey_overrides[ckey].
  2. Union + dedup (case-insensitive).
  3. Set sales_pitch = NULL (не може бути universal для всіх variants).
  4. Re-embed translation (OpenAI text-embedding-3-small).

Запуск:
    python -m scripts.aggregate_profile_content --country ua --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import text

from app.adapters.embeddings.openai_embedder import OpenAIEmbedder
from app.core.config import get_settings
from app.infrastructure.db.session import build_engine, build_session_factory

log = logging.getLogger("aggregate")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


LIST_FIELDS = ("addresses_problems", "target_audience", "benefits",
               "keywords", "cross_sell")


def _dedup_union(*lists: list[str]) -> list[str]:
    """Case-insensitive dedup union, preserves first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for lst in lists:
        for item in (lst or []):
            if not item:
                continue
            key = item.strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(item.strip())
    return out


async def _embed_text_for_translation(t_row, override_fields) -> str:
    """Build text для embedding з translation fields (mirror profile_repo._build_embed_text)."""
    parts: list[str] = []
    sd = t_row.get("short_description") or ""
    if sd:
        parts.append(sd)
    dd = t_row.get("detailed_description") or ""
    if dd:
        parts.append(dd)
    if override_fields["addresses_problems"]:
        parts.append("Problems: " + ", ".join(override_fields["addresses_problems"]))
    if override_fields["target_audience"]:
        parts.append("For: " + ", ".join(override_fields["target_audience"]))
    if override_fields["benefits"]:
        parts.append("Benefits: " + ", ".join(override_fields["benefits"]))
    if override_fields["keywords"]:
        parts.append("Keywords: " + ", ".join(override_fields["keywords"]))
    return " | ".join(parts)


async def amain() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True, choices=["ua", "pl", "gb"])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--language", default="uk")
    parser.add_argument("--skip-embed", action="store_true",
                        help="Skip re-embedding (use коли OpenAI недоступний)")
    args = parser.parse_args()

    embedder = None if args.skip_embed else OpenAIEmbedder()

    engine = build_engine()
    factory = build_session_factory(engine)

    try:
        async with factory() as session:
            r = await session.execute(text("""
                SELECT sp.id, sp.name,
                       t.id AS t_id,
                       t.short_description, t.detailed_description,
                       t.addresses_problems, t.target_audience, t.benefits,
                       t.keywords, t.cross_sell, t.ckey_overrides
                FROM public.service_profile sp
                JOIN public.service_profile_translation t
                  ON t.profile_id = sp.id AND t.language = :lang
                WHERE sp.country = :c
                ORDER BY sp.name
            """), {"c": args.country, "lang": args.language})
            rows = r.all()
            log.info("[%s] translations: %d", args.country, len(rows))

            updates: list[dict] = []
            for row in rows:
                pid, pname, t_id, sd, dd, ap, ta, b, kw, cs, ov_raw = row
                ov = dict(ov_raw or {})

                ap_lists = [list(ap or [])]
                ta_lists = [list(ta or [])]
                b_lists = [list(b or [])]
                kw_lists = [list(kw or [])]
                cs_lists = [list(cs or [])]
                for ck, ck_ov in ov.items():
                    if not isinstance(ck_ov, dict):
                        continue
                    if isinstance(ck_ov.get("addresses_problems"), list):
                        ap_lists.append(ck_ov["addresses_problems"])
                    if isinstance(ck_ov.get("target_audience"), list):
                        ta_lists.append(ck_ov["target_audience"])
                    if isinstance(ck_ov.get("benefits"), list):
                        b_lists.append(ck_ov["benefits"])
                    if isinstance(ck_ov.get("keywords"), list):
                        kw_lists.append(ck_ov["keywords"])
                    if isinstance(ck_ov.get("cross_sell"), list):
                        cs_lists.append(ck_ov["cross_sell"])

                agg = {
                    "addresses_problems": _dedup_union(*ap_lists),
                    "target_audience": _dedup_union(*ta_lists),
                    "benefits": _dedup_union(*b_lists),
                    "keywords": _dedup_union(*kw_lists),
                    "cross_sell": _dedup_union(*cs_lists),
                }

                updates.append({
                    "t_id": str(t_id), "pid": str(pid), "pname": pname,
                    "sd": sd or "", "dd": dd or "",
                    **agg,
                })

            log.info("[%s] computed aggregations for %d translations", args.country, len(updates))
            if not args.apply:
                log.info("DRY RUN. Sample first 3:")
                for u in updates[:3]:
                    log.info("  %s: ap=%d, ta=%d, b=%d, kw=%d, cs=%d",
                             u["pname"], len(u["addresses_problems"]),
                             len(u["target_audience"]), len(u["benefits"]),
                             len(u["keywords"]), len(u["cross_sell"]))
                return

            for idx, u in enumerate(updates, 1):
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
                    "ap": json.dumps(u["addresses_problems"], ensure_ascii=False),
                    "ta": json.dumps(u["target_audience"], ensure_ascii=False),
                    "b": json.dumps(u["benefits"], ensure_ascii=False),
                    "kw": json.dumps(u["keywords"], ensure_ascii=False),
                    "cs": json.dumps(u["cross_sell"], ensure_ascii=False),
                })
                if idx % 20 == 0:
                    await session.commit()
                    log.info("  %d/%d committed", idx, len(updates))
            await session.commit()
            log.info("[%s] All translations aggregated (sales_pitch=NULL).", args.country)

            if embedder:
                log.info("[%s] Re-embedding translations...", args.country)
                done = 0
                for u in updates:
                    parts = []
                    if u["sd"]:
                        parts.append(u["sd"])
                    if u["dd"]:
                        parts.append(u["dd"])
                    if u["addresses_problems"]:
                        parts.append("Problems: " + ", ".join(u["addresses_problems"]))
                    if u["target_audience"]:
                        parts.append("For: " + ", ".join(u["target_audience"]))
                    if u["benefits"]:
                        parts.append("Benefits: " + ", ".join(u["benefits"]))
                    if u["keywords"]:
                        parts.append("Keywords: " + ", ".join(u["keywords"]))
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
                        if done % 20 == 0:
                            await session.commit()
                            log.info("  embed: %d/%d", done, len(updates))
                    except Exception as exc:
                        log.warning("embed fail for %s: %s", u["pname"], exc)
                await session.commit()
                log.info("[%s] Re-embedded %d translations.", args.country, done)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(amain())

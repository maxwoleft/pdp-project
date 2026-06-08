# Profile Expert Content + AI Chat Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заповнити expert content (concerns/audience/benefits/keywords/sales_pitch/cross_sell) для всіх profiles + ckey overrides у UA/PL/GB БЕЗ OpenAI; додати OpenAI cron-автозаповнення для нових services; провести аудит AI chat profile-flow та запустити anti-hallucination chat tests.

**Architecture:**
- **Phase 1 (UI):** прибрати "В яких випадках робиться" textarea per-ckey (legacy), сховати Category Tree menu, переробити "Per-variant overrides" з зрозумілим CTA.
- **Phase 2 (Content):** Python-скрипт з embedded expert-templates per beauty-domain (manikyur, pedikyur, hair-color, hair-treatment, podology, masaj, vii, brovy, makijazh, depilation, apparat). Класифікує profile по name → applies template. Profile-level фільдз = aggregation; ckey-level override = тільки для real differentiators (tier, brand, target-area, partial vs full).
- **Phase 3 (OpenAI cron):** новий cron-job після auto_link знаходить profiles/ckeys без descriptions → OpenAI batch fill. Live для NEW services тільки.
- **Phase 4 (Audit):** грeп всіх місць де profile data споживається AI-агентом → перевірити що `concerns` (resolved per-ckey) дійсно передаються у LLM prompt; перевірити system-prompt на anti-hallucination instructions.
- **Phase 5 (Tests):** scripted chat сценаріями (ask for specific concern) → assert response contains DB-grounded services; антифабрикація — ban на non-DB service names.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 async + asyncpg, Jinja2, Alpine.js, OpenAI text-embedding-3-small (existing) + GPT-4o-mini (for Phase 3 only).

---

## File Structure

**Modify:**
- `app/admin/templates/profiles/detail.html` — UI tweaks (Phase 1)
- `app/admin/templates/base.html` (or sidebar) — hide Category Tree nav (Phase 1)
- `app/admin/profile_routes.py` — remove key-description endpoint usage in template (keep endpoint as no-op for backward compat) (Phase 1)
- `app/agents/agent_factory.py` or system-prompt — strengthen anti-hallucination instructions (Phase 4)

**Create:**
- `scripts/fill_profile_content.py` — Phase 2 bulk fill with expert templates
- `scripts/llm_fill_new_overrides.py` — Phase 3 OpenAI fill for unfilled
- `scripts/expert_templates/` — module з per-domain template dicts (UA-content)
  - `__init__.py`
  - `templates.py` (single file, domain→template map)
- `scripts/test_chat_anti_hallucination.py` — Phase 5 chat-driver tests
- `tests/test_profile_resolver.py` — unit для resolve_for_ckey (вже існує модуль)

**Skip Category Tree:**
- Знайти sidebar / nav template и закоментувати link.
- Сама route існує (`/admin/category-tree`), просто прибираємо вхід з UI.

---

## Phase 1: Frontend cleanup

### Task 1.1: Hide Category Tree з sidebar

**Files:**
- Modify: `app/admin/templates/base.html` (sidebar nav) — закоментувати category-tree link

- [ ] Grep sidebar template для "category-tree" / "Дерево категорій"
- [ ] Wrap link у HTML comment `<!-- {% comment %} ... {% endcomment %} -->` або `{% if False %}...{% endif %}`
- [ ] Deploy + verify сторінка показує без link

### Task 1.2: Прибрати legacy "В яких випадках робиться" textarea per-ckey

**Files:**
- Modify: `app/admin/templates/profiles/detail.html` — видалити violet-bg section

- [ ] Видалити блок `<div class="bg-violet-50 ...` з лоопу keys_breakdown
- [ ] Backend endpoint `/key-description` — залишити (cron auto_link все ще пише `profile.key_descriptions` для AI intent context); видалити лише UI

### Task 1.3: Покращити UI "Per-variant overrides"

**Files:**
- Modify: `app/admin/templates/profiles/detail.html` — переробити amber-bg section

Зміни:
- Button "Per-variant overrides ▼" більший і явно clickable.
- Підписи полів: укр-мовою з прикладами безпосередньо в placeholder.
- Hover effect, "Розгорнути для редагування" prompt текст.
- Зберегти Alpine.js логіку як є.

---

## Phase 2: Bulk content fill via expert templates

### Task 2.1: Створити expert templates module

**Files:**
- Create: `scripts/expert_templates/__init__.py`
- Create: `scripts/expert_templates/templates.py`

Структура `templates.py`:
```python
DOMAIN_TEMPLATES: dict[str, dict] = {
    "manikyur_classic": {
        "addresses_problems": ["неохайні кутикули", "тьмяні нігті", "відросла шкірка"],
        "target_audience": ["клієнти що цінують охайний догляд щотижня", "офісні працівники"],
        "benefits": ["естетика рук", "здоровий вигляд нігтів"],
        "keywords": ["манікюр", "класичний манікюр", "обрізний", "необрізний"],
        "sales_pitch": "Базовий догляд за нігтями — основа охайних рук",
        "cross_sell": ["Парафінотерапія", "Покриття гель-лак"],
    },
    "manikyur_gel": {...},
    "manikyur_yaponskyi": {...},
    "pedikyur_classic": {...},
    "pedikyur_yaponskyi": {...},
    "podology_korection": {...},
    "podology_orthos": {...},
    "hair_color_full": {...},
    "hair_color_roots": {...},
    "hair_highlight_airtouch": {...},
    "hair_highlight_balayage": {...},
    "hair_highlight_shatush": {...},
    "hair_treatment_botox": {...},
    "hair_treatment_keratin": {...},
    "hair_styling_basic": {...},
    "hair_styling_lux": {...},
    "hair_extension_install": {...},
    "hair_extension_maintenance": {...},
    "haircut_female": {...},
    "haircut_male": {...},
    "haircut_kids": {...},
    "haircut_bang": {...},
    "brow_correction": {...},
    "brow_lamination": {...},
    "brow_tint": {...},
    "lash_extension": {...},
    "lash_lamination": {...},
    "lash_tint": {...},
    "makeup_day": {...},
    "makeup_evening": {...},
    "makeup_special": {...},
    "makeup_permanent": {...},
    "massage_classic": {...},
    "massage_anticellulite": {...},
    "massage_lymphatic": {...},
    "massage_sport": {...},
    "massage_relax": {...},
    "massage_pregnancy": {...},
    "massage_face": {...},
    "depilation_wax": {...},
    "depilation_sugar": {...},
    "depilation_laser": {...},
    "cosmetology_chistka": {...},
    "cosmetology_piling": {...},
    "cosmetology_botox": {...},
    "cosmetology_filler": {...},
    "cosmetology_meso": {...},
    "cosmetology_carboxy": {...},
    "cosmetology_biorev": {...},
    "cosmetology_apparat_endosphere": {...},
    "cosmetology_apparat_kuma": {...},
    "cosmetology_apparat_lpg": {...},
    "cosmetology_apparat_icoone": {...},
    "consultation": {...},
    "spa_hands": {...},
    "spa_feet": {...},
    "nail_design": {...},
    "nail_repair": {...},
    "nail_extension": {...},
    "nail_strengthening": {...},
    "weaving": {...},
}
```

- [ ] Створити файл з ~50-60 templates покриваючих основні beauty домени (UA).
- [ ] Кожен template має 6 fields повністю на укр.
- [ ] Commit: feat: expert content templates for beauty domain

### Task 2.2: Класифікатор profile → template

**Files:**
- Create: `scripts/fill_profile_content.py`

Логіка:
- Завантажити всі profiles per country.
- Для кожного profile name → match keywords → pick template.
- Apply template до translation (default lang uk).
- Per-ckey overrides — тільки якщо ckey-token signals specific differentiator (наприклад "korin" → roots-only variant).

- [ ] Написати classifier function: `classify_profile(name, ckeys) -> template_key`.
- [ ] Logic: substring matching у lowercased name (e.g., "манікюр" + "класич" → "manikyur_classic").
- [ ] Fallback: якщо unmatched — warn, leave empty.
- [ ] Dry-run output: list (profile_name → template chosen).

### Task 2.3: Bulk apply скрипт

**Files:**
- Modify: `scripts/fill_profile_content.py`

- [ ] `--dry-run` показує що буде вставлено.
- [ ] `--apply` бере dry-run output → UPSERT translation rows (default lang uk).
- [ ] Per-ckey overrides: для ckeys що містять distinctive tokens (`korin`, `chastkovyi`, `top`, `art`) — override-ять specific fields.
- [ ] Run для UA, PL, GB.
- [ ] Commit per country: feat: fill expert content for {country} profiles

### Task 2.4: Verify coverage

- [ ] SQL count: профілів з non-empty addresses_problems / target_audience.
- [ ] Show 5 random samples.

---

## Phase 3: OpenAI cron auto-fill for new

### Task 3.1: Скрипт LLM-fill

**Files:**
- Create: `scripts/llm_fill_new_overrides.py`

Логіка:
- Знайти profiles/ckeys БЕЗ contents (addresses_problems empty AND ckey_overrides[ck] empty).
- Group by profile.
- Для кожного — call OpenAI chat completion з structured JSON output.
- Prompt включає: profile name, sample service name, category context.
- Validate JSON, write to DB.

- [ ] Виявити missing → list.
- [ ] OpenAI prompt template (укр output).
- [ ] Parser + validator.
- [ ] Apply.

### Task 3.2: Cron реєстрація

**Files:**
- Modify: `/etc/cron.d/aichat-sync` на droplet

- [ ] Додати `28 3 * * *` after auto_link.
- [ ] Логи у `/var/log/aichat/llm_overrides.log`.

---

## Phase 4: AI chat profile-flow audit

### Task 4.1: Trace data flow

- [ ] Список усіх місць де profile/translation/ckey-overrides читається для AI:
  - `app/agents/tools/builder.py` — search_by_concern, search_services, find_service_and_slots
  - `app/agents/agent_factory.py` (або де system prompt)
- [ ] Для кожного — verify що передає `concerns` (resolved) до LLM.

### Task 4.2: System prompt anti-hallucination

**Files:**
- Modify: `app/agents/agent_factory.py` (or where system prompt defined)

Додати або підсилити інструкцію:
> "Усі рекомендації послуг базуються ВИКЛЮЧНО на даних з tools (search_services / search_by_concern). Ніколи не пропонуй послугу, якої немає у tool result. Якщо нічого не знайшов — скажи 'на жаль, цього у нас немає'."

- [ ] Знайти system prompt.
- [ ] Якщо anti-hallucination instruction відсутня — додати.
- [ ] Deploy + restart aichat.

---

## Phase 5: Chat tests (anti-hallucination)

### Task 5.1: Test scenarios

**Files:**
- Create: `scripts/test_chat_anti_hallucination.py`

Тестові сценарії (UA, gb салон):
1. "Хочу пофарбувати корені" → response повинен містити service з ckey `farbuvannya_korin*` що exists у DB.
2. "Чи є у вас Airtouch?" → permanent profile "Airtouch" → service name з DB.
3. "Рекомендуй щось проти випадання волосся" → search_by_concern → response references real services.
4. "Чи робите ви масаж стоп?" → service з category "Massage" в DB.
5. "Що таке Endospheres?" → UA salon → real services з description.
6. NEGATIVE: "У вас є тату-видалення?" — Не повинно бути у DB → expect "ні, цього не пропонуємо".

Test harness:
- HTTP POST /api/chat/send-stream до prod.
- Parse response text.
- Assert: each mentioned service name must exist у `<country>.service.name`.
- Fail if any mentioned service NOT у DB (hallucination).

- [ ] Test scenarios definitions.
- [ ] HTTP driver.
- [ ] Assertion logic.
- [ ] Run + report.

---

## Self-Review checklist

- [ ] Phase 1 UI tweaks complete (3 changes).
- [ ] Phase 2: 50+ templates covering >90% UA/PL profiles.
- [ ] Phase 3: cron entry added.
- [ ] Phase 4: anti-hallucination prompt confirmed.
- [ ] Phase 5: all scenarios pass (no hallucinations).

---

## Open questions

- **Scope of templates:** ~60 templates покриють mainstream beauty domain. Niche UA-only profiles (Rejuran, Robolex, Stratosphere) — будуть unfilled на Phase 2 → Phase 3 cron auto-fill via OpenAI. Acceptable?
- **Per-ckey overrides:** автоматично fill тільки коли ckey має chief differentiator token. Решта inherit profile defaults. Acceptable?
- **GB profiles:** уже мають translations з expert content (з історії). Чи переписувати, чи лишити як є?

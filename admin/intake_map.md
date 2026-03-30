# AIOS → Super-Contract Intake Map

Reference document used by `sc-admin intake` to guide field extraction from AIOS context files.

---

## Mapping

| AIOS File | AIOS Section | → simply-connect context | Category |
|---|---|---|---|
| `context/business-info.md` | Organization Overview | `context/business.md` — Organization | business |
| `context/business-info.md` | Products / Services | `context/contracts.md` — Business Focus | contracts |
| `context/business-info.md` | Key Context | `context/business.md` — Key Facts | business |
| `context/personal-info.md` | Role / function | `context/preferences.md` — Working Style | preferences |
| `context/personal-info.md` | Working preferences | `context/preferences.md` — Standard Positions | preferences |
| `context/strategy.md` | Current priorities | `context/contracts.md` — Active Focus Areas | contracts |
| `context/strategy.md` | Goals | `context/contracts.md` — Contract Objectives | contracts |
| `context/current-data.md` | Key metrics | `context/business.md` — Key Facts | business |

---

## Process

The intake command (`sc-admin intake`) runs this process automatically:

1. Locates AIOS `context/` directory by walking up from the project root
2. Reads each AIOS context file
3. Uses Claude (haiku) to extract contract-relevant content per the mapping above
4. Creates a staging entry per file (source: `intake`)
5. Admin reviews via `sc-admin review` — nothing is committed automatically

---

## Notes

- Empty or placeholder sections in AIOS files are skipped
- Claude extracts only contract-relevant content — general business context is filtered
- Each AIOS file creates one staging entry (not one per section)
- The `general` category is used when a clear mapping cannot be determined; admin decides on review
- Run intake only once, or re-run and review carefully to avoid duplicate committed entries

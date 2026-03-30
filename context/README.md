# Committed Context

This directory contains the **authoritative, admin-controlled context** for Super-Contract.

---

## What This Is

Committed context is the ground truth that the agent treats as fully trusted. It is loaded at session start and informs every operator response.

Unlike staging entries, committed context has **passed a review gate** — either human or AI admin review via `sc-admin review`.

---

## Who Can Modify This

**Admin only.** Operators cannot write to this directory directly.

Updates flow through the staging layer:
1. Operator says "remember this" → staging entry created
2. Admin runs `sc-admin review` → approves entry
3. Approved content is appended to the appropriate file here

---

## Files

| File | Purpose |
|---|---|
| `business.md` | Org context — intake target from AIOS `business-info.md` |
| `parties.md` | Counterparties, clients, key contacts |
| `preferences.md` | Operator working style and standard positions — intake from AIOS `personal-info.md` |
| `contracts.md` | Active contracts, standard clauses, notable terms — intake from AIOS `strategy.md` |

---

## How to Populate

**From AIOS (recommended first step):**
```
sc-admin intake
sc-admin review
```

**Manual edit:**
Edit any file directly as admin. Keep the existing structure — add content under the appropriate sections.

**Via staging:**
Let operators capture updates naturally. Review and approve via `sc-admin review`.

---

## Keeping It Clean

- Keep entries factual and specific — avoid opinions or vague notes
- Remove outdated entries when contracts expire or parties change
- Run `sc-admin status` to monitor word counts and last-modified dates

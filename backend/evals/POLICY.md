# Skills annotation policy (eval labels)

Applied to `expected.skills` in `dataset.jsonl`. Label every JD consistently to this.

## A skill = a nameable technical capability. Include:
- **Named** languages, frameworks, libraries, tools, platforms, databases, cloud services
  (`python`, `react`, `node.js`, `postgresql`, `aws`, `kafka`, `docker`).
- **Technical concepts & engineering practices** explicitly named
  (`system design`, `distributed systems`, `data structures`, `algorithms`,
  `unit testing`, `integration testing`, `ci/cd`, `containerization`).
- **Domain / ML techniques** (`deep learning`, `machine learning`, `quantization`, `rag`,
  `model distillation`, `prompt engineering`).
- **Both required AND preferred / "nice-to-have"** skills.

## Exclude:
- Soft skills & traits: communication, teamwork, problem-solving, leadership, adaptability,
  organization, collaboration, reliability.
- Vague non-skills: programming, software development, software design, engineering design.

## Rules:
- Only skills **explicitly named**. Never infer (JD says "a modern language" with none named → add nothing).
- Canonical form: **lowercase, spaces (not underscores), bare name** —
  `aws` not "aws cloud services", `c` not "c programming", `api` not "restful apis".
- Split "X and Y" into two skills unless it's an established single term
  (`machine learning` stays; "modeling and simulation" → `modeling`, `simulation`).

## Scalar fields:
- `title`: the explicit role title, or `null` if none is stated (do NOT infer from team/context).
- `company_name` / `location`: explicit only, else `null`. Multiple locations → join with `" / "`.
- `work_type`: `remote` | `hybrid` | `in_person` | `null` (null if not stated).

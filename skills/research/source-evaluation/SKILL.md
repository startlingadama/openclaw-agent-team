---
name: source-evaluation
description: Evaluate the reliability of sources and flag conflicts.
---

# Source Evaluation

## Purpose

Evaluate the reliability of sources and flag conflicts.

## When to use

- After collecting several sources on the same question, before stating a conclusion.
- When two sources disagree, or when a claim rests on a single source.
- When the user asks how reliable a source, a figure or a claim is.

## Procedure

1. List the sources you actually read, with their URL.
2. For each one, note who publishes it (primary source, institution, press, company, blog,
   forum, anonymous), its date, and whether it gives its own evidence or repeats another source.
3. Assess reliability: a primary or official source outranks a secondary one; a recent source
   outranks an old one for anything that changes; several independent sources agreeing outrank
   one source repeated on several sites.
4. Compare the claims. Flag every conflict (different figures, dates or versions) and every
   claim supported by a single source only.
5. Say which claims you consider solid, which are uncertain, and what would settle them.

## Constraints

- Judge only sources you read in this run. Do not rate a site from its reputation alone.
- Do not hide a conflict to make the answer cleaner: report both versions and their sources.
- A source you could not open is a gap, not a reliable or unreliable source.
- Independence matters: sites that copy each other count as one source.

## Expected Output

A short table or list with one line per source (URL, publisher type, date, reliability:
high / medium / low, and a one-line reason), followed by:

- **Conflicts**: what disagrees, and between which sources.
- **Single-source claims**: what rests on one source only.
- **Confidence**: solid / uncertain for each main claim.

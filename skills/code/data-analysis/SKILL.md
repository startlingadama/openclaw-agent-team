---
name: data-analysis
description: Analyse tabular or structured data with short sandboxed Python scripts and report verified figures.
---

# Data Analysis

## Purpose

Compute figures and summaries from data files or from data returned by tools, with short Python
scripts run in the sandbox.

## When to use

- The user gives data (CSV, JSON, text) or a file in your sandbox directory and asks for totals,
  averages, trends, groupings, comparisons or checks.
- A tool returned structured data that needs filtering or aggregation before it is useful.
- Do not use it for data you cannot read from your sandbox directory or from a tool, and do not
  invent data to fill a gap.

## Procedure

1. Look at the data first: size, columns or keys, a few rows, missing values. Print little.
2. State the question as a computation: what is measured, on which rows, over which period.
3. Write a script using the standard library (`csv`, `json`, `statistics`, `collections`,
   `datetime`). Handle missing or malformed values explicitly and count what was skipped.
4. Run it with `code.execute` and read the output. Check the result for plausibility (row counts,
   totals against the parts, units, orders of magnitude).
5. Save a result file with `code.write_file` only when the user needs one.
6. Report figures with the population they were computed on.

## Constraints

- Only data given by the user or read through your authorized tools. No network in the sandbox.
- Every figure comes from a run in this task. Never estimate a figure by reasoning.
- Say how many rows were used and how many were dropped, and why.
- Do not print entire datasets; print aggregates and a few example rows.
- Personal or confidential data stays in the sandbox and out of memory.

## Expected Output

A short answer in the language of the request:

1. **Answer**: the figures asked for, with units and period.
2. **Method**: what was computed, on how many rows, what was excluded.
3. **Caveats**: data quality issues and what could not be checked.
4. **Files**: any file written, with its relative path.

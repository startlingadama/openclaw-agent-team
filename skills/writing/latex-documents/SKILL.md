---
name: latex-documents
description: Write a LaTeX document and compile it to PDF, correcting the source from the compilation log.
---

# LaTeX Documents

## Purpose

Produce a PDF from a LaTeX source you write: write the `.tex` file, compile it, and correct it
from the log until the PDF builds.

## When to use

- The user asks for a PDF, or for a document in LaTeX.
- A compilation failed and the source must be corrected.
- Do not write a PDF yourself: only `docs.compile_pdf` produces it.

## Procedure

1. Put the document in its own subdirectory (for example `my-report/report.tex`).
2. Write the source with `docs.write`. Use the class `article` or `report`, only allowed
   packages (`fontspec`, `geometry`, `hyperref`, `amsmath`, `graphicx`, `booktabs`, `listings`)
   and UTF-8 text: the engine is XeLaTeX, so accents and non-Latin scripts are written directly.
3. Compile with `docs.compile_pdf` (it needs human approval).
4. On an error, read the log excerpt in the result, fix the cause with `docs.patch`, and compile
   again. Do at most a few fix-and-compile rounds, then report what still fails.
5. Compile a second time when the document has references or a table of contents.

## Constraints

- No shell escape, no network, no file outside the document's directory, and a time limit: a
  source that tries otherwise fails.
- A refused approval or a failed compilation is not a PDF: say so.
- Do not change the content to hide an error you do not understand.
- Never publish or send the PDF: you have no tool for that.

## Expected Output

A short answer in the language of the request:

1. **Files**: the paths of the `.tex` source and of the PDF, when it was produced.
2. **Compilation**: succeeded, failed or not run, with the last error in one line.
3. **Limits**: what could not be verified.

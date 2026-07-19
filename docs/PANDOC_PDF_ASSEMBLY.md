# Pandoc PDF Assembly Workflow

## Purpose

Define a deterministic way to assemble active architecture and LLD documents under docs into a single PDF.

## Entry Point

Use:

- docs/__mdinclude.md

This file references domain include files in fixed order:

1. docs/qkd/__mdinclude.md
2. docs/kme/__mdinclude.md
3. docs/pqc/__mdinclude.md

## Method A: pre-expand includes, then run pandoc

If your mdinclude preprocessor supports !INCLUDE syntax, first expand docs/__mdinclude.md into a flat markdown file, then run pandoc.

Example (generic):

1. mdinclude preprocess docs/__mdinclude.md -> /tmp/docs_book.md
2. pandoc /tmp/docs_book.md -o docs_book.pdf

## Method B: explicit pandoc ordered list (no preprocessor)

If no mdinclude preprocessor is available, use pandoc directly with the same order:

pandoc \
  docs/TOC.md \
  docs/qkd/ARCHITECTURE.md \
  docs/qkd/CONFIG_GENERATION.md \
  docs/qkd/qkd_onbox_runtime_lld.md \
  docs/qkd/CLI_REFERENCE.md \
  docs/qkd/LOGGING_AND_CUSTOMER_REPORTING.md \
  docs/qkd/CERT_MANAGER.md \
  docs/kme/ARCHITECTURE.md \
  docs/kme/CLI_REFERENCE.md \
  docs/kme/VAULT_LOCALHOST_8200_SETUP.md \
  docs/pqc/THEORY_AND_STANDARDS.md \
  docs/pqc/GLOSSARY.md \
  -o docs_book.pdf

## Notes

- Active scope is docs only.
- archive/docs is excluded by design.
- Order is architecture-first, then detailed LLD/interface specs.

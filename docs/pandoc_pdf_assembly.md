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
  docs/toc.md \
  docs/qkd/architecture.md \
  docs/qkd/config_generation.md \
  docs/qkd/qkd_onbox_runtime_lld.md \
  docs/qkd/cli_reference.md \
  docs/qkd/logging_and_customer_reporting.md \
  docs/qkd/cert_manager.md \
  docs/kme/architecture.md \
  docs/kme/cli_reference.md \
  docs/kme/vault_localhost_8200_setup.md \
  docs/pqc/theory_and_standards.md \
  docs/pqc/glossary.md \
  -o docs_book.pdf

## Notes

- Active scope is docs only.
- archive/docs is excluded by design.
- Order is architecture-first, then detailed LLD/interface specs.

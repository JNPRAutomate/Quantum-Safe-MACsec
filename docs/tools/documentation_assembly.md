# Documentation Assembly and Validation

## 1. Canonical source

Only `docs/` is part of the maintained documentation set. Domain
`__mdinclude.md` files define assembly order. Historical architecture is
integrated into current domain documents and
[`architecture_evolution.md`](../architecture_evolution.md).

## 2. Assembly

The root `__mdinclude.md` source lists the global TOC, evolution, roadmap, and
each domain in reading order. It is an assembly manifest rather than a user
guide page. MkDocs renders the Markdown source tree into a separate static HTML
site using the navigation in `mkdocs.yml`.

Use a generated output directory; do not commit transient PDFs unless release
policy explicitly requires it.

## 3. Validation

Before publishing:

- every Markdown file has one H1;
- every local link resolves;
- every `!INCLUDE` resolves;
- no absolute developer path remains;
- no links target the removed `archive/` tree;
- commands match current CLI help/source;
- legacy behavior is marked as evolution, not current procedure;
- secrets and generated runtime state are absent;
- TOCs expose every canonical document;
- duplicate paragraphs or competing sources of truth are removed.

## 4. Diagram and code references

Use repository-relative links. When line numbers are likely to drift, link the
source file and name the function/symbol rather than embedding a stale numeric
range.

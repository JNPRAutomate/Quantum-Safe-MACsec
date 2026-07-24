# Unified Docs Include (Pandoc Assembly Entry)

This file is the canonical include entrypoint for assembling a single document.

## Global Order

1. TOC
2. QKD domain documents
3. KME domain documents
4. PQC domain documents

## mdinclude directives

!INCLUDE "TOC.md"

!INCLUDE "qkd/__mdinclude.md"
!INCLUDE "kme/__mdinclude.md"
!INCLUDE "pqc/__mdinclude.md"

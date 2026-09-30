# Unified Docs Include (Pandoc Assembly Entry)

This file is the canonical include entrypoint for assembling a single document.

## Global Order

1. TOC
2. Product and architecture roadmap
3. QKD domain documents
4. KME domain documents
5. PQC domain documents

## mdinclude directives

!INCLUDE "toc.md"
!INCLUDE "roadmap.md"

!INCLUDE "qkd/__mdinclude.md"
!INCLUDE "kme/__mdinclude.md"
!INCLUDE "pqc/__mdinclude.md"

# Ground Data Processing Tool 1.1.18

Changes from 1.1.17:

- Negotiation and sourcing record tables now have a WORK WEEK column, filled from each record's NEGO DATE / REPORT DATE using the Tuesday-to-Monday YYYY-WW rule.
- Publishing, editing and grid date changes write WORK WEEK automatically, so new or updated records always carry the work week that matches their date.
- Dataverse record rows were back-filled with their work week (Tuesday-to-Monday, YYYY-WW).

Validation: Python regression tests (171 passing, including new publish and work-week tests), Python compilation.

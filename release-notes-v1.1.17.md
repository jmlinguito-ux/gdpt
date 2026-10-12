# Ground Data Processing Tool 1.1.17

Changes from 1.1.16:

- Work weeks now run Tuesday to Monday for both sourcing and negotiation dates, and are written as YYYY-WW (for example 2026-41). The Tuesday-to-Monday week that contains January 1 is week 01 and belongs to the year of its closing Monday, so 12/30/2025 - 01/05/2026 is 2026-01.
- WORK WEEK published to the Productivity tables, the Work Week editor dropdown, the dashboard work-week filter and Build week checks all use the new format.
- Duplicate/distinction checks and LO-by-work-week counts derive each record's week from its date, so older rows stored as 35 or WW-35 still line up with new YYYY-WW values. Build tables that still hold WW-NN values continue to be read.

Dataverse was already updated to match: every sourcing and negotiation Productivity row now holds its Tuesday-to-Monday YYYY-WW value (the negotiation Productivity tables gained a Work Week column), and LO Occurrence by WW / LO Points by WW were recalculated for the moved weeks. Installing this release does not rewrite any further Dataverse rows.

Validation: Python regression tests (including new work-week tests), Python compilation, JavaScript syntax checks.

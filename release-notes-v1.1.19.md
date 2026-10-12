# Ground Data Processing Tool 1.1.19

Changes from 1.1.18:

- Publishing and editing negotiation and sourcing records now write WORK WEEK automatically, using the Tuesday-to-Monday YYYY-WW rule from NEGO DATE or REPORT DATE.
- Negotiation and sourcing record tables carry a WORK WEEK column in the app's record column set.

Dataverse was already updated to match: every record row with a date holds its correct work week.

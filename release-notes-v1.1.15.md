# Ground Data Processing Tool 1.1.15

Changes from 1.1.14:

- Generated Productivity CHECKER now identifies duplicates by AREA INDEX, date, matched negotiator/sourcer, and source classification. Different employees remain eligible. The lowest source ID is kept; later repeated employee rows are INVALID.
- Source visits are linked and checked before deriving Productivity validity. FIRST CONTACT describes chronology and does not restore an INVALID source. Unresolved links and calculations are shown for review and block publishing affected rows.
- POINTS and all six LO metrics are recalculated from the current Productivity workspace, without adding Records-only visits. Valid employee rows share one visit point; LO occurrences count distinct represented visits, using Wednesday-to-Tuesday work weeks and five-decimal credit.
- Matched-name, usability, area, date, LO edits and row deletion refresh calculations. LO metric fields are read-only in the generated workspace.
- Negotiation now loads an OFFER LETTER reference and calculates OFFER LETTER ACTION. A matching offer date produces OFFER SERVED, including the earliest visit. Other outcomes are INITIAL VISIT, REVISITED - WITH OFFER, and REVISITED - WITHOUT OFFER. Missing history or inputs require review. Sourcing excludes this feature.
- Historical Batangas sourcing names use repeated historical evidence, preserving former employees and independent validity failures.
- Publish duplicate detection uses normalized business visit identifiers plus the raw employee name to keep repeated publishing consistent.
- Refreshes bundled UI assets so installed updates show the new controls and calculation behavior.

Installing the update changes app behavior. It does not automatically rewrite Dataverse tables. Generated results remain staged until Publish/Save.

Validation: Python regression tests, JavaScript syntax and Offer Letter UI checks; GitHub builds installer, portable ZIP, full/delta update packages, and updater feed.

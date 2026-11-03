# Ground Data Processing Tool 1.1.20

Changes from 1.1.19:

- New PROJECT TAG column on productivity records, looked up from the Build table (same work-week match as BUILD).
- Derived BUILD now reads NO DATA when an area is in the Build table but its BUILD value is blank. An area that is not in the Build table still reads BALANCE. NO DATA is also available in the BUILD dropdown.
- Offer letter history falls back to the nego table when the existing-records source is sourcing or productivity.

Dataverse was already updated to match: BUILD and PROJECT TAG are filled for all tables (2026 and earlier), using CONSO / NON-CONSO / OUTSIDE CONSO (Batangas: TERRA BLOCK / NON-TERRA BLOCK), NO DATA, or BALANCE.

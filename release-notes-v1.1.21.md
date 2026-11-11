# Ground Data Processing Tool 1.1.21

Changes from 1.1.20:

- New PROJECT TAG column on productivity records, looked up from the Build table (same work-week match as BUILD).
- Derived BUILD now reads NO DATA when an area is in the Build table but its BUILD value is blank. An area that is not in the Build table still reads BALANCE. NO DATA is also available in the BUILD dropdown.

Dataverse was already updated to match: BUILD and PROJECT TAG are filled for all tables (2026 and earlier), using CONSO / NON-CONSO / OUTSIDE CONSO (Batangas: TERRA BLOCK / NON-TERRA BLOCK), NO DATA, or BALANCE.

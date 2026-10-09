# Ground Data Processing Tool 1.1.16

Changes from 1.1.15:

- Adds NO TEAM and NO GROUP to the Team and Group dropdowns in negotiation and sourcing workspaces, including when Team Composition has no entries.
- Adds Team and Group dropdowns for text fields in the Records/Productivity editor. Options include the roster choices, NO TEAM / NO GROUP, and existing values in the loaded rows so historical assignments remain selectable.
- Identical NO TEAM or NO GROUP source/corrected values use the existing MATCH comparison rule. Existing TEAM 0 / GROUP 0 choices and department filtering remain available.

Installing this release does not automatically rewrite Dataverse rows or change the existing roster-based calculation defaults. Editor changes remain staged until Save.

Validation: Python regression tests, Python compilation, JavaScript syntax checks, Offer Letter UI checks, and Windows release packaging.

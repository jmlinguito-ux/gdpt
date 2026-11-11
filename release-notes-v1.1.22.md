# Ground Data Processing Tool 1.1.22

Changes from 1.1.21:

- New Settings > Maintenance check: pick a records table (its productivity table is chosen automatically) and a work week, then click "Check for differences". The app reads that week's Build file from the BUILD PROJECT SITE on the MDM SharePoint site, shows which file it compared with and every row it would change, and updates BUILD and PROJECT TAG only after you confirm (with an optional CSV backup).
- Build values are read from the CONSO, BUILD or STATUS column (Batangas: TERRABLOCK). OUTSIDE CONSO and NON CONSO are written as NON-CONSO.
- BUILD and PROJECT TAG are derived together: an area not in the week-before Build reads BALANCE / BALANCE; found with a value reads the value and its project folder (e.g. QUEZON-1); found but blank reads NO DATA and its folder. If that week's Build is not loaded yet, both stay empty until a re-check fills them.
- Build view: sync the Build table straight from the BUILD PROJECT SITE.
